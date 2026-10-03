"""Refresh tokens.

A login gives a refresh token that works once: refreshing spends it and issues its
successor, and a token that was already spent is refused. The successor inherits the
expiry of the token it replaces, so a login ends refresh_token_expire_days after it
began however often it was refreshed, and logging out refuses the token presented."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import generate_refresh_token, hash_refresh_token
from app.config import settings
from app.models import RefreshToken, User


class RefreshRejected(Exception):
    """A refresh token cannot be exchanged. `reason` is for the log only: the client is
    always told the same thing, so it learns nothing about the token."""

    def __init__(self, reason: str, user_id: uuid.UUID | None = None):
        super().__init__(reason)
        self.reason = reason
        self.user_id = user_id


def _issue(db: Session, user_id: uuid.UUID, expires_at: datetime) -> str:
    raw = generate_refresh_token()
    db.add(RefreshToken(user_id=user_id, token_hash=hash_refresh_token(raw), expires_at=expires_at))
    return raw


def start_session(db: Session, user_id: uuid.UUID, now: datetime | None = None) -> str:
    """The refresh token of a new login."""
    now = now or datetime.now(UTC)
    return _issue(db, user_id, now + timedelta(days=settings.refresh_token_expire_days))


def rotate_refresh_token(db: Session, raw_token: str, now: datetime | None = None) -> tuple[uuid.UUID, str]:
    """Spends `raw_token` and returns (user_id, successor token). Raises RefreshRejected."""
    now = now or datetime.now(UTC)
    # FOR UPDATE: two requests with one token must not both see it unspent. The second
    # waits for the first to commit and then finds it spent.
    stored = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token)).with_for_update()
    ).scalar_one_or_none()
    if stored is None:
        raise RefreshRejected("unknown")
    if stored.revoked_at is not None:
        raise RefreshRejected("revoked", stored.user_id)
    if stored.expires_at <= now:
        raise RefreshRejected("expired", stored.user_id)

    # A locked account is read from the database now, like on every request: its refresh token
    # must not buy it a new access token.
    user = db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise RefreshRejected("disabled", stored.user_id)

    stored.revoked_at = now
    return stored.user_id, _issue(db, stored.user_id, stored.expires_at)


def end_session(db: Session, raw_token: str, now: datetime | None = None) -> uuid.UUID | None:
    """Logout: refuses the token presented from now on. Returns the user, or None when the
    token is unknown."""
    now = now or datetime.now(UTC)
    stored = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
    ).scalar_one_or_none()
    if stored is None:
        return None
    if stored.revoked_at is None:
        stored.revoked_at = now
        db.commit()
    return stored.user_id


def revoke_all(db: Session, user_id: uuid.UUID, now: datetime | None = None) -> None:
    """Ends every login of the user: their refresh tokens stop working. Does not commit."""
    now = now or datetime.now(UTC)
    for stored in db.execute(
        select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    ).scalars():
        stored.revoked_at = now
