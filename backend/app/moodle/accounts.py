"""Which Moodle account is a lecturer: the one they linked by proving its username and password
(POST /moodle/account/link)."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import User
from app.moodle import client

RELINK = (
    "Tài khoản Moodle đã liên kết của bạn không còn trên Moodle. "
    "Hãy nhập lại tên đăng nhập và mật khẩu Moodle ở mục \"Tài khoản Moodle\" để liên kết lại rồi thử lại."
)


def require_moodle_user_id(user: User) -> int:
    """The Moodle account the lecturer linked. Without one, a 422 the lecturer can act on."""
    if user.moodle_user_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Bạn chưa liên kết tài khoản Moodle. "
                "Hãy nhập tên đăng nhập và mật khẩu Moodle ở mục \"Tài khoản Moodle\" rồi thử lại."
            ),
        )
    return user.moodle_user_id


def forget_if_gone(db: Session, user: User) -> bool:
    """Clears the stored link when Moodle no longer has that user (an admin deleted or merged it),
    so the lecturer is asked to link again. Lets Moodle errors through."""
    if user.moodle_user_id is None or client.user_exists(user.moodle_user_id):
        return False
    user.moodle_user_id = None
    db.commit()
    return True


def enrol_linked_teacher(db: Session, user: User, moodle_course_id: int) -> None:
    """Enrols the lecturer's linked Moodle account as the course's teacher. When Moodle refuses
    because that account is gone, the stale link is cleared and the lecturer is told to link
    again (422); any other Moodle error goes through."""
    teacher_id = require_moodle_user_id(user)
    try:
        client.enrol_as_teacher(moodle_course_id, teacher_id)
    except client.MoodleError:
        if forget_if_gone(db, user):
            raise HTTPException(status_code=422, detail=RELINK) from None
        raise
