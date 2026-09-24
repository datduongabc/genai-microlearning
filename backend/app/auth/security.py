import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import bcrypt
import jwt

from app.config import settings

# bcrypt only ever reads this many bytes of a password.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        # Callers check the limit first (password_policy); hashing a longer password
        # would silently ignore its tail, so refuse rather than store that.
        raise ValueError(f"password is longer than {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Accounts made before bcrypt 5 with a longer password were hashed from its first
    # 72 bytes (older versions cut it silently), so a longer input is compared by that
    # prefix instead of being refused, which would lock those users out.
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8")[:MAX_PASSWORD_BYTES], hashed_password.encode("utf-8")
        )
    except ValueError:  # a stored hash that is not a bcrypt hash
        return False


def create_access_token(subject: Any) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(subject), "exp": expire}
    return jwt.encode(payload, settings.signing_key(), algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    try:
        # exp and sub are required: PyJWT only checks an expiry that is present,
        # and a token without one would never stop working.
        return jwt.decode(
            token,
            settings.signing_key(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None


def token_user_id(payload: dict | None) -> UUID | None:
    """The user a validated token names, or None when its subject is not a user id
    (a signed token can still carry anything in `sub`)."""
    if not payload:
        return None
    try:
        return UUID(str(payload["sub"]))
    except (KeyError, ValueError):
        return None


def generate_refresh_token() -> str:
    """Chuỗi ngẫu nhiên entropy cao (256 bit), không phải JWT -- refresh
    token cần tra cứu DB để kiểm tra revoked_at (thu hồi thật), nên không có
    lý do tự chứa claim như access token."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """sha256, không phải bcrypt: token đã entropy cao (sinh ngẫu nhiên, khác
    mật khẩu người dùng chọn), không cần cố ý làm chậm -- cần nhanh vì mỗi
    lần refresh đều tra cứu theo hash này."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
