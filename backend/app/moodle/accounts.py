"""Which Moodle account is a lecturer: the one they linked, which has their email (compared
case-insensitively)."""

import secrets

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.accounts import EDU_ONLY, is_edu_email
from app.models import User
from app.moodle import client


class MoodleAccountMissing(Exception):
    """The lecturer's Moodle account cannot be linked: several share the email, or none can be made."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


RELINK = (
    "Tài khoản Moodle đã liên kết của bạn không còn trên Moodle. "
    "Hãy bấm \"Liên kết tài khoản Moodle\" để liên kết lại rồi thử lại."
)


def require_moodle_user_id(user: User) -> int:
    """The Moodle account the lecturer chose to link (POST /moodle/account/link). One whose email
    merely exists in Moodle is not linked until they say so: a 422 the lecturer can act on."""
    if user.moodle_user_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Bạn chưa liên kết tài khoản Moodle. "
                "Hãy bấm \"Liên kết tài khoản Moodle\" rồi thử lại."
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


def _moodle_names(name: str) -> tuple[str, str]:
    """Moodle wants a first and a last name, both non-empty."""
    parts = name.split()
    return (" ".join(parts[:-1]) or parts[0], parts[-1]) if parts else ("-", "-")


def _moodle_password() -> str:
    # Moodle's default policy wants a digit, an upper and a lower case letter and a symbol. The
    # lecturer's app password is never shared with Moodle; they set their own there ("forgot
    # password"), or an admin does.
    return f"Aa1!{secrets.token_urlsafe(18)}"


def ensure_moodle_user(user: User) -> tuple[int, bool]:
    """The lecturer's Moodle account: the one that already has their email (case-insensitive) is
    linked, never duplicated, and when there is none it is created with that email. Returns
    (Moodle user id, created). Raises MoodleAccountMissing when several accounts share the
    email, and lets Moodle errors through."""
    email = user.email.strip().lower()
    ids = client.find_users_by_email([email])[email]
    if len(ids) > 1:
        raise MoodleAccountMissing(
            f"Có nhiều tài khoản Moodle cùng email {user.email}, không xác định được tài khoản để liên kết."
        )
    if ids:
        return ids[0], False
    # Moodle itself accepts only edu addresses (allowemailaddresses), and so does this app.
    if not is_edu_email(email):
        raise MoodleAccountMissing(f"Không tạo được tài khoản Moodle: {EDU_ONLY}")
    first, last = _moodle_names(user.name)
    return client.create_user(email, first, last, _moodle_password()), True
