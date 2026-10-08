"""Reserve module generations in the same transaction that enqueues their jobs."""
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Course, User
from app.moodle import client
from app.moodle.errors import MOODLE_FAILURES


def has_full_access(db: Session, user: User) -> bool:
    if user.moodle_user_id is None:
        return False
    linked_ids = set(db.scalars(select(Course.moodle_course_id).where(
        Course.owner_id == user.id, Course.moodle_course_id.is_not(None))))
    if not linked_ids:
        return False
    try:
        identity = client.teacher_courses(user.moodle_user_id)
    except MOODLE_FAILURES:
        return False
    return bool(identity['exists'] and linked_ids.intersection(course['id'] for course in identity['courses']))


def reserve_generations(db: Session, user: User, count: int = 1) -> None:
    # Different documents can generate concurrently; serialize counters on the account.
    db.refresh(user, with_for_update=True)
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Tài khoản đã bị vô hiệu hóa.")
    if has_full_access(db, user):
        return
    today = datetime.now(UTC).date()
    used = user.generation_count if user.generation_day == today else 0
    limit = settings.non_teacher_daily_generation_limit
    if used + count > limit:
        raise HTTPException(status_code=429, detail=(
            f"Bạn được sinh tối đa {limit} mô-đun mỗi ngày (UTC). "
            "Liên kết môn học Moodle mà bạn có quyền quản lý hoạt động để dùng đầy đủ."
        ))
    user.generation_day = today
    user.generation_count = used + count
