"""Local accounts and real login sessions for HTTP tests."""
import uuid

from app.auth import sessions
from app.auth.security import create_access_token, hash_password
from app.db import SessionLocal
from app.models import User

STRONG_PASSWORD = "Tt7!kQm2#vXz"


def make_account(email: str, password: str = STRONG_PASSWORD, name: str = "Test", is_admin: bool = False) -> User:
    with SessionLocal() as db:
        user = User(email=email.strip().lower(), name=name, hashed_password=hash_password(password), is_admin=is_admin)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def link_moodle_account(user_id, moodle_user_id: int | None = None) -> None:
    with SessionLocal() as db:
        db.get(User, user_id).moodle_user_id = moodle_user_id if moodle_user_id is not None else 10_000 + uuid.uuid4().int % 10**9
        db.commit()


def start_session(user: User) -> dict:
    with SessionLocal() as db:
        refresh_token = sessions.start_session(db, user.id)
        db.commit()
    return {"access_token": create_access_token(subject=user.id), "refresh_token": refresh_token,
            "token_type": "bearer", "user": {"id": str(user.id), "email": user.email,
            "name": user.name, "is_admin": user.is_admin}}


def create_and_login(client, email: str, password: str = STRONG_PASSWORD, is_admin: bool = False) -> dict:
    make_account(email, password, is_admin=is_admin)
    return client.post("/auth/login", json={"email": email, "password": password}).json()


def admin_headers(email: str | None = None) -> dict:
    import uuid

    from fastapi.testclient import TestClient

    from app.main import app
    email = email or f"admin-{uuid.uuid4().hex[:10]}@example.com"
    token = create_and_login(TestClient(app), email, is_admin=True)["access_token"]
    return {"Authorization": f"Bearer {token}"}
