"""Local account administration, activation and failed-job management."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.auth import audit, sessions
from app.auth.accounts import AccountError, create_account
from app.auth.audit import log_admin_event
from app.auth.password_policy import check_password
from app.auth.rate_limit import limiter, user_or_address
from app.auth.security import hash_password
from app.db import get_db
from app.jobs.failures import EDITOR_RETRIED_JOBS
from app.jobs.queue import EDITOR_RETRY_MESSAGE, retry_failed_job
from app.models import JobType, User
from app.moodle.accounts import unlink_account

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

MAX_FAILED_JOBS = 200
# Why the admin skips a failure in the lecturer's input, shown beside it.
LECTURER_INPUT_MESSAGE = "Lỗi do nội dung giảng viên gửi lên: giảng viên tự sửa rồi thử lại."


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
def list_failed_jobs(job_type: list[JobType] | None = Query(None), db: Session = Depends(get_db)):
    """The failed jobs of every lecturer. Reads the view admin_failed_jobs and nothing else:
    job data plus the document file name and course name, with no document, module or chat
    content, and without the failures a lecturer already generated again."""
    query = "SELECT * FROM admin_failed_jobs"
    params: dict = {"limit": MAX_FAILED_JOBS}
    # Several types at once: a video's script, pictures and render are one filter on the page.
    if job_type:
        query += " WHERE job_type::text = ANY(:job_types)"
        params["job_types"] = [t.value for t in job_type]
    rows = db.execute(text(query + " ORDER BY created_at DESC LIMIT :limit"), params).mappings().all()
    return [dict(row) for row in rows]


@router.get("/counts")
def admin_counts(db: Session = Depends(get_db)):
    """Totals for the admin tab and filter labels, counted in full whatever filter or cap the
    lists use."""
    users, inactive = db.execute(
        select(func.count(), func.count().filter(User.is_active.is_(False))).select_from(User)
    ).one()
    failed = db.execute(text("SELECT job_type, count(*) FROM admin_failed_jobs GROUP BY job_type")).all()
    return {"users": users, "inactive_users": inactive, "failed_jobs": dict(failed)}


@router.post("/jobs/retry")
@limiter.limit("30/minute", key_func=user_or_address)
def retry_jobs(
    request: Request, payload: RetryJobsRequest, db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Retries the failed jobs picked on the page. A job that is not failed, that its lecturer
    already generated again, or that does not exist is skipped, and the rest are still retried.

    Two kinds are skipped with the reason in `reasons`. A failure in the lecturer's input (a file that
    cannot be read, a request that cannot be met) is theirs to fix: the admin retries what the
    system failed, once its cause is fixed. A failed picture or render is retried only by the video
    editor, which locks the video, waits for a job in flight and counts the day's limit.
    retry_failed_job refuses the first kind to an admin as well, so the rule holds without this check."""
    ids = list(dict.fromkeys(payload.job_ids))
    # Read through the view: row level security hides other lecturers' jobs from the admin's session.
    rows = db.execute(text("SELECT id, job_type::text AS job_type, system_failure FROM admin_failed_jobs "
                           "WHERE id = ANY(:ids)"), {"ids": ids}).all()
    editor_types = {t.value for t in EDITOR_RETRIED_JOBS}
    reasons = {row.id: EDITOR_RETRY_MESSAGE if row.job_type in editor_types else LECTURER_INPUT_MESSAGE
               for row in rows if row.job_type in editor_types or not row.system_failure}
    retried = [job_id for job_id in ids if job_id not in reasons and retry_failed_job(db, job_id)]
    db.commit()
    log_admin_event(
        audit.ADMIN_RETRY_JOBS, request, actor_id=admin.id,
        detail={"job_ids": [str(j) for j in retried], "skipped": len(ids) - len(retried)},
    )
    return {"retried": retried, "skipped": [j for j in ids if j not in set(retried)], "reasons": reasons}


@router.post("/users/{user_id}/moodle-unlink")
def unlink_user_moodle_account(
    request: Request, user_id: uuid.UUID, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    user = _target(db, user_id)
    unlink_account(db, user)
    log_admin_event(audit.ADMIN_UNLINK_MOODLE, request, actor_id=admin.id, target_user_id=user.id)
    return _user_out(user, "not_linked")


_ERASE_REFUSED = {
    "not_found": (404, "User not found"),
    "owns_courses": (409, "Tài khoản này còn môn học. Hãy chuyển môn học cho giảng viên khác, hoặc xóa chúng, rồi xóa dữ liệu."),
}


@router.post("/users/{user_id}/erase")
@limiter.limit("10/minute", key_func=user_or_address)
def erase_user(
    request: Request, user_id: uuid.UUID, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    """Erases a person's data at their request: who they were, their chats and their logins. Ratings they
    gave a study stay, with no name. The work is the database function erase_user (migration 0048)."""
    outcome = db.scalar(select(func.erase_user(user_id)))
    if outcome in _ERASE_REFUSED:
        db.rollback()
        status, detail = _ERASE_REFUSED[outcome]
        raise HTTPException(status_code=status, detail=detail)
    db.commit()
    log_admin_event(audit.ADMIN_ERASE_USER, request, actor_id=admin.id, target_user_id=user_id)
    return {"erased": True}


@router.get("/users/{user_id}/courses")
def list_user_courses(user_id: uuid.UUID, db: Session = Depends(get_db)):
    """The courses a person owns, for handing them over (admin_user_courses, migration 0050)."""
    rows = db.execute(text("SELECT id, name, moodle_course_id, created_at FROM admin_user_courses(:u)"),
                      {"u": user_id}).all()
    return [{"id": r.id, "name": r.name, "moodle_course_id": r.moodle_course_id, "created_at": r.created_at}
            for r in rows]


class TransferCourseRequest(BaseModel):
    new_owner_id: uuid.UUID
    keep_previous_as_teacher: bool = True


_TRANSFER_REFUSED = {
    "course_not_found": (404, "Course not found"),
    "owner_not_eligible": (422, "Người nhận phải là giảng viên có tài khoản đang hoạt động."),
    "same_owner": (409, "Môn học đã thuộc về giảng viên này."),
}


@router.post("/courses/{course_id}/transfer")
@limiter.limit("30/minute", key_func=user_or_address)
def transfer_course(
    request: Request, course_id: uuid.UUID, payload: TransferCourseRequest, db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Hands a course, with everything under it, to another lecturer (transfer_course, migration 0050)."""
    outcome = db.scalar(select(func.transfer_course(course_id, payload.new_owner_id, payload.keep_previous_as_teacher)))
    if outcome in _TRANSFER_REFUSED:
        db.rollback()
        status, detail = _TRANSFER_REFUSED[outcome]
        raise HTTPException(status_code=status, detail=detail)
    db.commit()
    log_admin_event(audit.ADMIN_TRANSFER_COURSE, request, actor_id=admin.id, target_user_id=payload.new_owner_id,
                    detail={"course_id": str(course_id), "kept_previous": payload.keep_previous_as_teacher})
    return {"transferred": True}
