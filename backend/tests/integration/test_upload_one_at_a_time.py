"""A course accepts a new upload only once its previous document has finished
processing (embedded) or failed. The rule must hold on the server, not only in
the UI: a page refresh or a second browser tab loses any client-side state."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Course, Document, DocumentStatus, User
from tests.upload_helpers import upload_document

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_upload_dir(monkeypatch, tmp_path):
    """Uploads must never land in the real upload directory, not even when the
    guard under test is broken and a request that should be refused succeeds."""
    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))


@pytest.fixture
def world():
    """A teacher with two courses; everything created is removed afterwards
    (the ON DELETE CASCADE from courses also removes documents and jobs, so
    no queued job is left behind for the job-queue tests)."""
    db = SessionLocal()
    try:
        user = User(
            email=f"upload-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False
        )
        db.add(user)
        db.flush()
        course = Course(name="Upload guard A", owner_id=user.id)
        other = Course(name="Upload guard B", owner_id=user.id)
        db.add_all([course, other])
        db.commit()
        ids = {"user": user.id, "course": course.id, "other": other.id}
    finally:
        db.close()

    yield {**ids, "token": create_access_token(ids["user"])}

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id.in_([ids["course"], ids["other"]])).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _add_document(course_id, status: DocumentStatus) -> None:
    db = SessionLocal()
    try:
        db.add(Document(course_id=course_id, filename=f"{status.value}.txt", status=status))
        db.commit()
    finally:
        db.close()


def _document_count(course_id) -> int:
    db = SessionLocal()
    try:
        return db.query(Document).filter(Document.course_id == course_id).count()
    finally:
        db.close()


def _upload(world, course_id):
    return upload_document(
        client, course_id, "notes.html", b"<p>hello world</p>", {"Authorization": f"Bearer {world['token']}"},
    )


@pytest.mark.parametrize("status", [DocumentStatus.uploaded, DocumentStatus.processing])
def test_upload_is_refused_while_a_document_of_the_course_is_still_being_processed(world, status):
    _add_document(world["course"], status)

    response = _upload(world, world["course"])

    assert response.status_code == 409
    assert _document_count(world["course"]) == 1  # nothing new was created


@pytest.mark.parametrize("status", [DocumentStatus.embedded, DocumentStatus.failed])
def test_upload_is_accepted_once_previous_documents_are_finished(world, status):
    _add_document(world["course"], status)

    response = _upload(world, world["course"])

    assert response.status_code == 200
    assert _document_count(world["course"]) == 2


def test_a_document_being_processed_in_another_course_does_not_block_uploads(world):
    _add_document(world["other"], DocumentStatus.processing)

    response = _upload(world, world["course"])

    assert response.status_code == 200
