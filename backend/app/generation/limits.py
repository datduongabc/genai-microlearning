"""Reserve module generations, and the pictures and renders of videos, in the same transaction that
enqueues their jobs."""
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Course, User
from app.moodle import client
from app.moodle.errors import MOODLE_FAILURES


def has_full_access(db: Session, user: User) -> bool:
    """Whether the account generates without the daily limit: it is linked to a Moodle course its
    Moodle user may still teach. Asks Moodle, so the caller calls it before locking any row: Moodle
    can take seconds (4 s when its host name does not resolve), and a row held meanwhile keeps the
    lecturer's other requests waiting."""
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


def reserve_generations(db: Session, user: User, *, full_access: bool, count: int = 1, noun: str = "mô-đun") -> None:
    """`full_access` is has_full_access, asked by the caller before it locked anything."""
    # Different documents can generate concurrently; serialize counters on the account.
    db.refresh(user, with_for_update=True)
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Tài khoản đã bị vô hiệu hóa.")
    if full_access:
        return
    today = datetime.now(UTC).date()
    used = user.generation_count if user.generation_day == today else 0
    limit = settings.non_teacher_daily_generation_limit
    if used + count > limit:
        raise HTTPException(status_code=429, detail=(
            f"Mỗi ngày bạn được tạo tối đa {limit} {noun}. "
            "Liên kết môn học với một khóa học Moodle bạn quản lý để bỏ giới hạn này."
        ))
    user.generation_day = today
    user.generation_count = used + count


def _count_today(db: Session, user: User, kind: str, count: int, limit: int, refusal: str) -> None:
    """`count` more of `kind` today, the account row locked as reserve_generations locks it."""
    db.refresh(user, with_for_update=True)
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Tài khoản đã bị vô hiệu hóa.")
    today = datetime.now(UTC).date()
    used = getattr(user, f"{kind}_count") if getattr(user, f"{kind}_day") == today else 0
    if used + count > limit:
        raise HTTPException(status_code=429, detail=refusal.format(limit=limit, left=max(limit - used, 0)))
    setattr(user, f"{kind}_day", today)
    setattr(user, f"{kind}_count", used + count)


def reserve_video_redraws(db: Session, user: User, images: int) -> None:
    """Counts the pictures asked for, not the requests: a request for five costs five times one.

    For every account, Moodle-linked or not: each picture is paid, and nobody else is accountable for them."""
    _count_today(db, user, "video_redraw", images, settings.video_redraws_per_day,
                 "Mỗi ngày bạn được vẽ tối đa {limit} hình, hôm nay còn {left} hình. Hãy quay lại vào ngày mai.")


def reserve_video_render(db: Session, user: User) -> None:
    _count_today(db, user, "video_render", 1, settings.video_renders_per_day,
                 "Mỗi ngày (UTC) bạn được dựng video tối đa {limit} lần. Hãy quay lại vào ngày mai.")
