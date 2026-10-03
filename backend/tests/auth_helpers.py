"""Making accounts and logging in through the real HTTP API, for tests that need a user.

There is no public sign-up: accounts are made by an admin (or the create-admin command), so a
test makes one the way they do, through create_account."""

from app.auth.accounts import create_account
from app.db import SessionLocal
from app.models import User, UserRole

STRONG_PASSWORD = "Tt7!kQm2#vXz"


def make_account(
    email: str, password: str = STRONG_PASSWORD, name: str = "Test", role: UserRole = UserRole.teacher
) -> User:
    with SessionLocal() as db:
        # Tests use example.com addresses all over; the edu rule has tests of its own.
        user = create_account(db, email=email, name=name, password=password, role=role, edu_only=False)
        db.refresh(user)  # the commit expired it, and it is read after the session is closed
        return user


def create_and_login(client, email: str, password: str = STRONG_PASSWORD, role: UserRole = UserRole.teacher) -> dict:
    """Returns the login response body (access_token, refresh_token, user)."""
    make_account(email, password, role=role)
    return client.post("/auth/login", json={"email": email, "password": password}).json()


def admin_headers(email: str | None = None) -> dict:
    """Authorization header of a new admin. The admin is not removed afterwards: an email is
    unique per call, and the account does not own anything."""
    import uuid

    from fastapi.testclient import TestClient

    from app.main import app

    email = email or f"admin-{uuid.uuid4().hex[:10]}@example.com"
    token = create_and_login(TestClient(app), email, role=UserRole.admin)["access_token"]
    return {"Authorization": f"Bearer {token}"}
