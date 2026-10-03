"""The admin's endpoints: lecturer accounts and the failed jobs of every lecturer.

Every route needs an admin (require_admin answers anyone else 404, like a missing thing). What an
admin reaches in the database is narrower still (migration 0007): only failed jobs and the
document file name and course name beside them, never what a lecturer wrote or uploaded."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.auth import audit, sessions
from app.auth.accounts import AccountError, create_account
from app.auth.audit import log_admin_event
from app.auth.password_policy import check_password
from app.auth.rate_limit import limiter, user_or_address
from app.auth.security import hash_password
from app.db import get_db
from app.jobs.queue import retry_failed_job
from app.models import JobType, User, UserRole
from app.moodle import client as moodle_client
from app.moodle.errors import MOODLE_FAILURES

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

MAX_FAILED_JOBS = 200


class CreateUserRequest(BaseModel):
    # Only caps on the size of the body: email, name and password are checked by create_account,
    # which answers in words the admin can act on instead of a schema error.
    email: str = Field(max_length=254)
    name: str = Field(max_length=100)
    password: str = Field(max_length=1024)


class SetPasswordRequest(BaseModel):
    password: str = Field(max_length=1024)


class RetryJobsRequest(BaseModel):
    job_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_FAILED_JOBS)


def _moodle_statuses(users: list[User]) -> dict[uuid.UUID, str]:
    """Per lecturer: "linked" (exactly one Moodle account with their email), "not_found",
    "ambiguous" (several), or "unknown" for all of them when Moodle cannot be reached."""
    lecturers = [u for u in users if u.role == UserRole.teacher]
    try:
        ids_by_email = moodle_client.find_users_by_email([u.email for u in lecturers])
    except MOODLE_FAILURES:
        return {u.id: "unknown" for u in lecturers}
    found = {}
    for user in lecturers:
        count = len(ids_by_email.get(user.email.strip().lower(), []))
        found[user.id] = "not_found" if count == 0 else "linked" if count == 1 else "ambiguous"
    return found


def _user_out(user: User, moodle_status: str | None = None) -> dict:
    return {
        "id": user.id, "email": user.email, "name": user.name, "role": user.role,
        "is_active": user.is_active, "created_at": user.created_at, "moodle_status": moodle_status,
    }


def _target(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.created_at.desc(), User.email)).all()
    moodle = _moodle_statuses(list(users))
    return [_user_out(u, moodle.get(u.id)) for u in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute", key_func=user_or_address)
def create_lecturer(
    request: Request, payload: CreateUserRequest, db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        user = create_account(
            db, email=payload.email, name=payload.name, password=payload.password, role=UserRole.teacher
        )
    except AccountError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    log_admin_event(audit.ADMIN_CREATE_USER, request, actor_id=admin.id, target_user_id=user.id)
    return _user_out(user)


def _set_active(
    request: Request, db: Session, admin: User, user_id: uuid.UUID, *, active: bool
) -> dict:
    # Lock every admin row: two admins locking each other out at the same moment must not both
    # find "another active admin" still there.
    db.execute(select(User.id).where(User.role == UserRole.admin).with_for_update()).all()
    user = _target(db, user_id)
    if not active:
        if user.id == admin.id:
            raise HTTPException(status_code=409, detail="Không thể tự khóa tài khoản của chính mình.")
        if user.role == UserRole.admin and user.is_active:
            others = db.scalar(
                select(User.id).where(
                    User.role == UserRole.admin, User.is_active.is_(True), User.id != user.id
                ).limit(1)
            )
            if others is None:
                raise HTTPException(status_code=409, detail="Không thể khóa admin hoạt động cuối cùng.")
    user.is_active = active
    if not active:
        # Their access token stops working at once (get_current_user); this ends the logins that
        # could mint a new one.
        sessions.revoke_all(db, user.id)
    db.commit()
    log_admin_event(
        audit.ADMIN_ENABLE_USER if active else audit.ADMIN_DISABLE_USER, request,
        actor_id=admin.id, target_user_id=user.id,
    )
    return _user_out(user)


@router.post("/users/{user_id}/disable")
def disable_user(
    request: Request, user_id: uuid.UUID, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    return _set_active(request, db, admin, user_id, active=False)


@router.post("/users/{user_id}/enable")
def enable_user(
    request: Request, user_id: uuid.UUID, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    return _set_active(request, db, admin, user_id, active=True)


@router.post("/users/{user_id}/password")
@limiter.limit("30/minute", key_func=user_or_address)
def reset_password(
    request: Request, user_id: uuid.UUID, payload: SetPasswordRequest, db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = _target(db, user_id)
    weakness = check_password(payload.password, email=user.email, name=user.name)
    if weakness is not None:
        raise HTTPException(status_code=422, detail=weakness)
    user.hashed_password = hash_password(payload.password)
    # A new password ends the old logins; the password itself is never logged.
    sessions.revoke_all(db, user.id)
    db.commit()
    log_admin_event(audit.ADMIN_RESET_PASSWORD, request, actor_id=admin.id, target_user_id=user.id)
    return {"status": "ok"}


@router.get("/jobs/failed")
def list_failed_jobs(job_type: JobType | None = None, db: Session = Depends(get_db)):
    """The failed jobs of every lecturer. Reads the view admin_failed_jobs and nothing else:
    job data plus the document file name and course name, with no document, module or chat
    content, and without the failures a lecturer already generated again."""
    query = "SELECT * FROM admin_failed_jobs"
    params: dict = {"limit": MAX_FAILED_JOBS}
    if job_type is not None:
        query += " WHERE job_type = :job_type"
        params["job_type"] = job_type.value
    rows = db.execute(text(query + " ORDER BY created_at DESC LIMIT :limit"), params).mappings().all()
    return [dict(row) for row in rows]


@router.post("/jobs/retry")
@limiter.limit("30/minute", key_func=user_or_address)
def retry_jobs(
    request: Request, payload: RetryJobsRequest, db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Retries the failed jobs picked on the page. A job that is not failed, that its lecturer
    already generated again, or that does not exist is skipped, and the rest are still retried."""
    ids = list(dict.fromkeys(payload.job_ids))
    retried = [job_id for job_id in ids if retry_failed_job(db, job_id)]
    db.commit()
    log_admin_event(
        audit.ADMIN_RETRY_JOBS, request, actor_id=admin.id,
        detail={"job_ids": [str(j) for j in retried], "skipped": len(ids) - len(retried)},
    )
    return {"retried": retried, "skipped": [j for j in ids if j not in set(retried)]}
