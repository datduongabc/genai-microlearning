"""A lecturer logs in with their Moodle username and password. Moodle is where lecturer accounts
and passwords live: the app asks Moodle whether they are right (local_lms_bridge_verify_login),
and the first time they are, the app makes the local lecturer account and links it to that Moodle
user (users.moodle_user_id), or links an existing local account that has the same email. The
Moodle password is used for that one check and is neither stored nor logged. Admins are not
handled here: their password is local (scripts/create_admin.py)."""

import secrets
from dataclasses import dataclass

import httpx
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.accounts import EDU_ONLY, is_edu_email
from app.auth.security import hash_password
from app.models import User, UserRole
from app.moodle import client
from app.moodle.errors import MOODLE_REFUSED

MAX_NAME_LENGTH = 100
WRONG_CREDENTIALS = "Tên đăng nhập hoặc mật khẩu không đúng."
MOODLE_DOWN = "Moodle tạm thời không phản hồi. Vui lòng thử lại sau ít phút."
ACCOUNT_DISABLED = "Tài khoản đã bị khóa. Hãy liên hệ admin để được hỗ trợ."


class LoginRefused(Exception):
    """The login cannot go on. `status_code` and `detail` are the answer to the person, `reason`
    is for the log only."""

    def __init__(self, status_code: int, detail: str, reason: str):
        super().__init__(reason)
        self.status_code = status_code
        self.detail = detail
        self.reason = reason


@dataclass
class MoodleLogin:
    user: User
    # "existing" (already linked), "linked" (an existing local account with that email) or "created".
    outcome: str


def _moodle_failure(error: Exception) -> LoginRefused:
    """Moodle could not answer. A refusal of the web service itself is a setup problem for the admin;
    anything else is worth trying again."""
    if isinstance(error, client.MoodleError) and error.errorcode in ("accessexception", "invalidtoken"):
        return LoginRefused(503, MOODLE_REFUSED, "moodle_refused")
    return LoginRefused(503, MOODLE_DOWN, "moodle_unavailable")


def _by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(func.lower(User.email) == email).first()


def login_with_moodle(db: Session, username: str, password: str) -> MoodleLogin:
    """The lecturer behind a Moodle username and password, made or linked on the first success.
    Raises LoginRefused. Commits any account it makes or links."""
    if not password:
        raise LoginRefused(401, WRONG_CREDENTIALS, "moodle_credentials")
    try:
        moodle_user_id = client.verify_login(username, password)
        if moodle_user_id is None:
            raise LoginRefused(401, WRONG_CREDENTIALS, "moodle_credentials")
        profile = client.get_user(moodle_user_id)
    except (client.MoodleError, httpx.HTTPError) as error:
        raise _moodle_failure(error) from error
    if profile is None:
        raise LoginRefused(401, WRONG_CREDENTIALS, "moodle_credentials")

    # Said only after Moodle accepted the password, so a stranger learns nothing about accounts.
    linked = db.query(User).filter(User.moodle_user_id == moodle_user_id).first()
    if linked is not None:
        _must_be_usable_lecturer(linked)
        return MoodleLogin(linked, "existing")

    email = profile["email"]
    if not email:
        raise LoginRefused(
            403, "Tài khoản Moodle của bạn chưa có email. Hãy nhờ admin Moodle cập nhật email trường.", "no_email"
        )
    if not is_edu_email(email):
        raise LoginRefused(403, f"Email của tài khoản Moodle không dùng được: {EDU_ONLY}", "not_edu_email")

    same_email = _by_email(db, email)
    if same_email is not None:
        return _link_existing(db, same_email, moodle_user_id)
    return _create(db, email, profile["name"], moodle_user_id)


def _must_be_usable_lecturer(user: User) -> None:
    if user.role != UserRole.teacher:
        # An admin's account is local; a Moodle login must never open it.
        raise LoginRefused(403, "Email của tài khoản Moodle trùng với một tài khoản quản trị. Hãy liên hệ admin.", "admin_email")
    if not user.is_active:
        raise LoginRefused(403, ACCOUNT_DISABLED, "disabled")


def _link_existing(db: Session, user: User, moodle_user_id: int) -> MoodleLogin:
    _must_be_usable_lecturer(user)
    if user.moodle_user_id is not None and user.moodle_user_id != moodle_user_id:
        # Another Moodle user holds this email's account, unless that one is gone from Moodle.
        try:
            still_there = client.user_exists(user.moodle_user_id)
        except (client.MoodleError, httpx.HTTPError) as error:
            raise _moodle_failure(error) from error
        if still_there:
            raise LoginRefused(
                409,
                "Email này đã được liên kết với một tài khoản Moodle khác. Hãy liên hệ admin để được hỗ trợ.",
                "email_linked_elsewhere",
            )
    user.moodle_user_id = moodle_user_id
    db.commit()
    return MoodleLogin(user, "linked")


def _create(db: Session, email: str, name: str, moodle_user_id: int) -> MoodleLogin:
    # Lecturers never log in with this password (their password is Moodle's), so it is random and nobody knows it.
    user = User(
        email=email, name=(name or email.split("@")[0])[:MAX_NAME_LENGTH],
        hashed_password=hash_password(secrets.token_urlsafe(32)), role=UserRole.teacher,
        moodle_user_id=moodle_user_id,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two first logins at once: the unique email lets one in, and the other is the same person.
        db.rollback()
        existing = _by_email(db, email)
        if existing is None:
            raise
        return _link_existing(db, existing, moodle_user_id)
    return MoodleLogin(user, "created")
