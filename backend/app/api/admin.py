"""Local account administration, activation and failed-job management."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
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
from app.models import JobType, User
from app.moodle.accounts import unlink_account

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

MAX_FAILED_JOBS = 200


class RetryJobsRequest(BaseModel):
    job_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_FAILED_JOBS)


def _moodle_statuses(users: list[User]) -> dict[uuid.UUID, str]:
    """Per lecturer: "linked" when they hold a stored Moodle link (users.moodle_user_id, set at their
    confirmed Moodle link), else "not_linked". Read from the database, so it needs no call to Moodle."""
    return {u.id: "linked" if u.moodle_user_id is not None else "not_linked" for u in users if not u.is_admin}


def _course_counts(db: Session) -> dict[uuid.UUID, int]:
    """Courses per owner, from the view admin_course_counts: row level security hides every
    lecturer's courses from the admin, and the view lets through only these counts."""
    rows = db.execute(text("SELECT owner_id, course_count FROM admin_course_counts")).all()
    return {owner_id: count for owner_id, count in rows}


def _user_out(
    user: User, moodle_status: str | None = None, course_count: int | None = None,
) -> dict:
    return {
        "id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin,
        "is_active": user.is_active, "created_at": user.created_at, "moodle_status": moodle_status,
        "course_count": course_count,
    }


def _target(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/users")
def list_users(is_active: bool | None = None, db: Session = Depends(get_db)):
    query = select(User).order_by(User.created_at.desc(), User.email)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    users = db.scalars(query).all()
    moodle = _moodle_statuses(list(users))
    courses = _course_counts(db)
    return [
        _user_out(u, moodle.get(u.id), courses.get(u.id, 0))
        if not u.is_admin else _user_out(u, moodle.get(u.id))
        for u in users
    ]


def _set_active(
    request: Request, db: Session, admin: User, user_id: uuid.UUID, *, active: bool
) -> dict:
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not active and user.is_admin:
        raise HTTPException(status_code=409, detail="Không thể vô hiệu hóa tài khoản admin.")
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


class CreateUserRequest(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(max_length=100)
    password: str = Field(max_length=1024, repr=False)


class SetPasswordRequest(BaseModel):
    password: str = Field(max_length=1024, repr=False)


@router.post("/users", status_code=201)
@limiter.limit("10/minute", key_func=user_or_address)
def create_user(request: Request, payload: CreateUserRequest, db: Session = Depends(get_db),
                admin: User = Depends(require_admin)):
    try:
        user = create_account(db, email=payload.email, name=payload.name, password=payload.password)
    except AccountError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    log_admin_event(audit.ADMIN_CREATE_USER, request, actor_id=admin.id, target_user_id=user.id)
    return _user_out(user, "not_linked", 0)


@router.post("/users/{user_id}/password")
@limiter.limit("10/minute", key_func=user_or_address)
def reset_password(request: Request, user_id: uuid.UUID, payload: SetPasswordRequest,
                   db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = _target(db, user_id)
    weakness = check_password(payload.password, email=user.email, name=user.name)
    if weakness is not None:
        raise HTTPException(status_code=422, detail=weakness)
    user.hashed_password = hash_password(payload.password)
    sessions.revoke_all(db, user.id)
    db.commit()
    log_admin_event(audit.ADMIN_RESET_PASSWORD, request, actor_id=admin.id, target_user_id=user.id)
    return _user_out(user)


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


@router.post("/users/{user_id}/moodle-unlink")
def unlink_user_moodle_account(
    request: Request, user_id: uuid.UUID, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    user = _target(db, user_id)
    unlink_account(db, user)
    log_admin_event(audit.ADMIN_UNLINK_MOODLE, request, actor_id=admin.id, target_user_id=user.id)
    return _user_out(user, "not_linked")
