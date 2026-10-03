"""Creating accounts. There is no public sign-up: only an admin (app/api/admin.py) or the
create-admin command (scripts/create_admin.py) calls this."""

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.password_policy import check_password
from app.auth.security import hash_password
from app.models import User, UserRole

_EMAIL = TypeAdapter(EmailStr)


class AccountError(Exception):
    """An account cannot be made. `status_code` is the HTTP answer for it, `detail` is
    written for the person who asked."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def normalize_email(raw: str) -> str:
    """Case differences ("A@x.com" vs "a@x.com") must not make two accounts of what a person
    considers one email, and the Moodle match is case-insensitive too."""
    try:
        return _EMAIL.validate_python(raw.strip()).lower()
    except ValidationError as error:
        raise AccountError(422, "Email không hợp lệ.") from error


def clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise AccountError(422, "Họ tên không được để trống")
    return name


def create_account(db: Session, *, email: str, name: str, password: str, role: UserRole) -> User:
    """Adds and commits the account. Raises AccountError for a weak password or a taken email."""
    email = normalize_email(email)
    name = clean_name(name)
    weakness = check_password(password, email=email, name=name)
    if weakness is not None:
        raise AccountError(422, weakness)

    taken = AccountError(409, "Email đã được đăng ký.")
    if db.query(User.id).filter(User.email == email).first() is not None:
        raise taken
    user = User(email=email, name=name, hashed_password=hash_password(password), role=role)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as error:
        # Two requests with one email that both passed the check above: the unique index lets
        # exactly one in, and the other is a conflict like any duplicate.
        db.rollback()
        raise taken from error
    db.commit()
    return user
