"""Making accounts and logging in through the real HTTP API, for tests that need a user.

Accounts are made by a lecturer signing up, an admin, or the create-admin command. A test
inserts the row itself, since tests use example.com addresses all over and create_account takes
only edu ones (that rule, and the rest of create_account, has tests of its own)."""

from app.auth.security import hash_password
from app.db import SessionLocal
from app.models import User, UserRole

STRONG_PASSWORD = "Tt7!kQm2#vXz"


def make_account(
    email: str, password: str = STRONG_PASSWORD, name: str = "Test", role: UserRole = UserRole.teacher
) -> User:
    with SessionLocal() as db:
        user = User(email=email.strip().lower(), name=name, hashed_password=hash_password(password), role=role)
        db.add(user)
        db.commit()
        db.refresh(user)  # the commit expired it, and it is read after the session is closed
        return user


def link_moodle_account(user_id, moodle_user_id: int = 4242) -> None:
    """Stores the lecturer's Moodle link, as their Yes in the pop-up would (POST /moodle/account/link),
    for tests that are not about linking it."""
    with SessionLocal() as db:
        db.get(User, user_id).moodle_user_id = moodle_user_id
        db.commit()


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
