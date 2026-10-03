"""Which Moodle account is a lecturer: the one with the same email, compared case-insensitively."""

import secrets

from fastapi import HTTPException

from app.auth.accounts import EDU_ONLY, is_edu_email
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
            "Hãy liên kết tài khoản Moodle của bạn (nút \"Liên kết tài khoản Moodle\") rồi thử lại."
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
