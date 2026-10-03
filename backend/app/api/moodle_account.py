"""The lecturer's own Moodle account, which they choose to link: the one that already has their
email (case-insensitive) is linked, and when there is none it is created. A lecturer must have it
before linking a course (courses.py) or pushing (moodle.py)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth import audit
from app.auth.audit import log_auth_event
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.models import User, UserRole
from app.moodle import client as moodle_client
from app.moodle.accounts import MoodleAccountMissing, ensure_moodle_user
from app.moodle.errors import MOODLE_FAILURES, moodle_failure

router = APIRouter(prefix="/moodle", tags=["moodle"], dependencies=[Depends(get_current_user)])


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
    """Whether the lecturer's Moodle account is linked, i.e. whether exactly one Moodle user has
    their email. The pop-up that recommends linking asks this."""
    user = _lecturer(current_user)
    email = user.email.strip().lower()
    try:
        count = len(moodle_client.find_users_by_email([email])[email])
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db) from e
    return {"email": user.email, "status": "not_linked" if count == 0 else "linked" if count == 1 else "ambiguous"}


@router.post("/account/link")
@limiter.limit("10/minute", key_func=user_or_address)
def link_account(
    request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """The "Yes" of the pop-up: link the Moodle user with the lecturer's email, or create it."""
    user = _lecturer(current_user)
    try:
        _, created = ensure_moodle_user(user)
    except MoodleAccountMissing as refused:
        raise HTTPException(status_code=422, detail=refused.message) from refused
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db) from e
    log_auth_event(audit.MOODLE_ACCOUNT_LINKED, request, user_id=user.id, email=user.email, detail={"created": created})
    return {"email": user.email, "status": "linked", "created": created}
