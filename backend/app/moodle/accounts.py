"""Which Moodle account is a lecturer: the one with the same email, compared case-insensitively."""

from fastapi import HTTPException

from app.models import User
from app.moodle import client


class MoodleAccountMissing(Exception):
    """No single Moodle account matches the lecturer's email."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def moodle_user_id(user: User) -> int:
    """The id of the lecturer's Moodle account. Raises MoodleAccountMissing when there is none
    (or more than one, which cannot tell who the teacher is), and lets Moodle errors through."""
    ids = client.find_users_by_email([user.email])[user.email.strip().lower()]
    if not ids:
        raise MoodleAccountMissing(
            f"Không tìm thấy tài khoản Moodle nào có email {user.email}. "
            "Hãy nhờ admin tạo tài khoản Moodle với đúng email này rồi thử lại."
        )
    if len(ids) > 1:
        raise MoodleAccountMissing(
            f"Có nhiều tài khoản Moodle cùng email {user.email}, không xác định được giáo viên. "
            "Hãy nhờ admin Moodle xử lý trùng email rồi thử lại."
        )
    return ids[0]


def require_moodle_user_id(user: User) -> int:
    """moodle_user_id for an endpoint: a missing account is a 422 the lecturer can act on."""
    try:
        return moodle_user_id(user)
    except MoodleAccountMissing as missing:
        raise HTTPException(status_code=422, detail=missing.message) from missing
