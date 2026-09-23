"""The teacher chooses at upload whether a document is a lecture or a textbook."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Course, Document, DocumentKind, User, UserRole

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_upload_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))


@pytest.fixture
def world():
    db = SessionLocal()
    try:
        user = User(
            email=f"kind-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", role=UserRole.teacher
        )
        db.add(user)
        db.flush()
        course = Course(name="Upload kind", owner_id=user.id)
        db.add(course)
        db.commit()
        ids = {"user": user.id, "course": course.id}
    finally:
        db.close()

    yield {**ids, "token": create_access_token(ids["user"])}

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _upload(world, **params):
    return client.post(
        "/documents",
        params={"course_id": world["course"], **params},
        files={"file": ("notes.txt", b"hello world", "text/plain")},
        headers={"Authorization": f"Bearer {world['token']}"},
    )


def _stored_kind(document_id) -> DocumentKind:
    db = SessionLocal()
    try:
        return db.get(Document, uuid.UUID(document_id)).kind
    finally:
        db.close()


def test_a_textbook_upload_is_stored_as_a_textbook(world):
    response = _upload(world, kind="textbook")

    assert response.status_code == 200
    assert _stored_kind(response.json()["id"]) == DocumentKind.textbook


def test_an_upload_without_a_kind_is_a_lecture(world):
    response = _upload(world)

    assert response.status_code == 200
    assert _stored_kind(response.json()["id"]) == DocumentKind.lecture


def test_an_unknown_kind_is_rejected(world):
    assert _upload(world, kind="novel").status_code == 422
