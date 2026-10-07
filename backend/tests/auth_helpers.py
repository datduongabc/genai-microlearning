"""Making accounts and sessions, for tests that need a user.

A lecturer's account is made from Moodle at their first login (test_moodle_login.py), and an admin's
by the create-admin command. A test inserts the row itself, since tests use example.com addresses all
over, and gets a session the way a login does without going through it: lecturers log in through
Moodle, which a test replaces only where the login is what is tested."""

from app.auth import sessions
from app.auth.security import create_access_token, hash_password
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
    """Stores the lecturer's Moodle link, as their first Moodle login would, for tests that are
    not about linking it."""
    with SessionLocal() as db:
        db.get(User, user_id).moodle_user_id = moodle_user_id
        db.commit()


def start_session(user: User) -> dict:
    """What a successful login returns (access_token, refresh_token, user), for an existing account."""
    with SessionLocal() as db:
        refresh_token = sessions.start_session(db, user.id)
        db.commit()
    return {
        "access_token": create_access_token(subject=user.id),
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {"id": str(user.id), "email": user.email, "name": user.name, "role": user.role.value},
    }


def create_and_login(client, email: str, password: str = STRONG_PASSWORD, role: UserRole = UserRole.teacher) -> dict:
    """Makes the account and returns the login response body (access_token, refresh_token, user).
    An admin logs in for real, with the local password; a lecturer is given the session a Moodle
    login would."""
    user = make_account(email, password, role=role)
    if role == UserRole.admin:
        return client.post("/auth/login", json={"username": email, "password": password}).json()
    return start_session(user)


def admin_headers(email: str | None = None) -> dict:
    """Authorization header of a new admin. The admin is not removed afterwards: an email is
    unique per call, and the account does not own anything."""
    import uuid

    from fastapi.testclient import TestClient

    from app.main import app

    email = email or f"admin-{uuid.uuid4().hex[:10]}@example.com"
    token = create_and_login(TestClient(app), email, role=UserRole.admin)["access_token"]
    return {"Authorization": f"Bearer {token}"}
