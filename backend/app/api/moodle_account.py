"""The lecturer's own Moodle account, as the "Tài khoản Moodle" section shows it. It is linked at the
lecturer's first Moodle login (app/auth/moodle_login.py), so this only reports it, and clears a link
Moodle no longer honours (the Moodle user was deleted or merged) so that the next login makes it again."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.models import User, UserRole
from app.moodle.accounts import forget_if_gone
from app.moodle.errors import MOODLE_FAILURES, moodle_failure

router = APIRouter(prefix="/moodle", tags=["moodle"], dependencies=[Depends(get_current_user)])


@router.get("/account")
@limiter.limit("60/minute", key_func=user_or_address)
def get_account(
    request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """"linked" while the lecturer has a stored Moodle link and Moodle still has that user,
    "not_linked" otherwise (the stale link is cleared)."""
    # Only a lecturer has a Moodle account; an admin gets the same answer as for a missing page.
    if current_user.role != UserRole.teacher:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        linked = current_user.moodle_user_id is not None and not forget_if_gone(db, current_user)
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db) from e
    return {"email": current_user.email, "status": "linked" if linked else "not_linked"}
