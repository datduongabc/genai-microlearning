from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import User
from app.moodle import client

RELINK = (
    "Tài khoản Moodle đã liên kết của bạn không còn trên Moodle. "
    "Hãy liên kết lại tài khoản Moodle rồi thử lại."
)


def require_moodle_user_id(user: User) -> int:
    """The Moodle account the lecturer linked. Without one, a 422 the lecturer can act on."""
    if user.moodle_user_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Bạn chưa liên kết tài khoản Moodle. "
                "Hãy liên kết tài khoản Moodle rồi thử lại."
            ),
        )
    return user.moodle_user_id


def teacher_courses(db: Session, user: User) -> list[dict]:
    identity = client.teacher_courses(require_moodle_user_id(user))
    if not identity["exists"]:
        user.moodle_user_id = None
        db.commit()
        raise HTTPException(status_code=422, detail=RELINK)
    return identity["courses"]


def require_course_author(db: Session, user: User, course_id: int) -> None:
    if not any(course["id"] == course_id for course in teacher_courses(db, user)):
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý hoạt động trong khóa học Moodle này.")
