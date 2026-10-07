"""The lecturer's own Moodle account, which they link by entering its username and password: the
app asks Moodle whether they are right and links only then. The Moodle password is used for that one
check and is neither stored nor logged. The admin makes the Moodle accounts (in Moodle); the app
creates none. The link is stored (users.moodle_user_id), and a lecturer must have it before linking
a course (courses.py) or pushing (moodle.py)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth import audit
from app.auth.audit import log_auth_event
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.models import User, UserRole
from app.moodle import client as moodle_client
from app.moodle.accounts import forget_if_gone
from app.moodle.errors import MOODLE_FAILURES, moodle_failure

router = APIRouter(prefix="/moodle", tags=["moodle"], dependencies=[Depends(get_current_user)])

WRONG_CREDENTIALS = "Tên đăng nhập hoặc mật khẩu Moodle không đúng."
ALREADY_LINKED = "Tài khoản Moodle này đã được liên kết với một giảng viên khác."


class LinkRequest(BaseModel):
    # Only caps on the size of the body: Moodle is the judge of whether they are right.
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024, repr=False)


def _lecturer(user: User) -> User:
    # Only a lecturer has a Moodle account to link; an admin gets the same answer as for a missing page.
    if user.role != UserRole.teacher:
        raise HTTPException(status_code=404, detail="Not found")
    return user


@router.get("/account")
@limiter.limit("60/minute", key_func=user_or_address)
def get_account(
    request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Whether the lecturer has linked their Moodle account, for the "Tài khoản Moodle" section:
    "linked" while they have linked it and Moodle still has that user, "not_linked" before or once
    it is gone (the stale link is cleared)."""
    user = _lecturer(current_user)
    try:
        linked = user.moodle_user_id is not None and not forget_if_gone(db, user)
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db) from e
    return {"email": user.email, "status": "linked" if linked else "not_linked"}


@router.post("/account/link")
@limiter.limit("5/minute", key_func=user_or_address)
def link_account(
    request: Request, payload: LinkRequest, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Links the Moodle account whose username and password the lecturer entered, and remembers
    which one it is. Nothing is linked unless Moodle says they are right."""
    user = _lecturer(current_user)
    try:
        moodle_user_id = moodle_client.verify_login(payload.username.strip(), payload.password)
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db) from e
    if moodle_user_id is None:
        log_auth_event(audit.MOODLE_ACCOUNT_LINK_REFUSED, request, user_id=user.id, email=user.email)
        raise HTTPException(status_code=422, detail=WRONG_CREDENTIALS)
    taken = db.query(User.id).filter(User.moodle_user_id == moodle_user_id, User.id != user.id).first()
    if taken is not None:
        log_auth_event(
            audit.MOODLE_ACCOUNT_LINK_REFUSED, request, user_id=user.id, email=user.email,
            detail={"reason": "linked_to_another_lecturer"},
        )
        raise HTTPException(status_code=409, detail=ALREADY_LINKED)
    user.moodle_user_id = moodle_user_id
    db.commit()
    log_auth_event(audit.MOODLE_ACCOUNT_LINKED, request, user_id=user.id, email=user.email)
    return {"email": user.email, "status": "linked"}
