from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.models import User
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
    try:
        linked = current_user.moodle_user_id is not None and not forget_if_gone(db, current_user)
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db) from e
    return {"email": current_user.email, "status": "linked" if linked else "not_linked"}
