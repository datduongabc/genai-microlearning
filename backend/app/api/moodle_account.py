"""The lecturer's own Moodle account, which they choose to link: the one that already has their
email (case-insensitive) is linked, and when there is none it is created. The link is stored
(users.moodle_user_id), and a lecturer must have it before linking a course (courses.py) or
pushing (moodle.py)."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth import audit
from app.auth.audit import log_auth_event
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.models import User, UserRole
from app.moodle import client as moodle_client
from app.moodle.accounts import MoodleAccountMissing, ensure_moodle_user, forget_if_gone
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
    """Whether the lecturer has linked their Moodle account, for the course page's "Tài khoản
    Moodle" section and the pop-up: "linked" only once they linked it and while Moodle still has
    that user, "not_linked" before (even when Moodle already has a user with their email) or once
    it is gone (the stale link is cleared), and "ambiguous" when several Moodle users share the
    email, which cannot be linked. `prompt` says whether to show the pop-up: only to an unlinked
    lecturer who never answered it, so a link that goes stale later does not bring it back."""
    user = _lecturer(current_user)
    email = user.email.strip().lower()
    try:
        if user.moodle_user_id is not None and not forget_if_gone(db, user):
            status = "linked"
        else:
            count = len(moodle_client.find_users_by_email([email])[email])
            status = "ambiguous" if count > 1 else "not_linked"
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db) from e
    prompt = status != "linked" and user.moodle_prompted_at is None
    return {"email": user.email, "status": status, "prompt": prompt}


def _answered_prompt(user: User) -> None:
    if user.moodle_prompted_at is None:
        user.moodle_prompted_at = datetime.now(UTC)


@router.post("/account/prompt/dismiss")
@limiter.limit("10/minute", key_func=user_or_address)
def dismiss_prompt(
    request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """The "No" of the pop-up: it is not shown again, and the account can still be linked from
    the course page."""
    user = _lecturer(current_user)
    _answered_prompt(user)
    db.commit()
    return {"prompt": False}


@router.post("/account/link")
@limiter.limit("10/minute", key_func=user_or_address)
def link_account(
    request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """The "Yes" of the pop-up, or the course page's "Liên kết tài khoản Moodle": link the Moodle
    user with the lecturer's email, or create it, and remember which one it is. Either way the
    pop-up has been answered."""
    user = _lecturer(current_user)
    try:
        moodle_user_id, created = ensure_moodle_user(user)
    except MoodleAccountMissing as refused:
        raise HTTPException(status_code=422, detail=refused.message) from refused
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db) from e
    user.moodle_user_id = moodle_user_id
    _answered_prompt(user)
    db.commit()
    log_auth_event(audit.MOODLE_ACCOUNT_LINKED, request, user_id=user.id, email=user.email, detail={"created": created})
    return {"email": user.email, "status": "linked", "created": created}
