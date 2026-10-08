"""GET /documents tells the client which documents currently have a module
being generated, so the UI can show it after a refresh or from another tab
instead of relying on state that only lives in the browser."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Course, Document, DocumentStatus, Job, JobStatus, JobType, User

client = TestClient(app)


@pytest.fixture
def world():
    db = SessionLocal()
    try:
        user = User(
            email=f"gen-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False
        )
        db.add(user)
        db.flush()
        course = Course(name="Generating flag", owner_id=user.id)
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


def _add_document(course_id, filename: str, job_status: JobStatus | None, job_type=JobType.module_generation):
    """A ready document, optionally with one job in the given state."""
    db = SessionLocal()
    try:
        doc = Document(course_id=course_id, filename=filename, status=DocumentStatus.embedded, raw_text="text")
        db.add(doc)
        db.flush()
        if job_status is not None:
            db.add(Job(job_type=job_type, step="generate", status=job_status, document_id=doc.id))
        db.commit()
        return doc.id
    finally:
        db.close()


def _generating_by_filename(world) -> dict[str, bool]:
    response = client.get(
        "/documents", params={"course_id": world["course"]}, headers={"Authorization": f"Bearer {world['token']}"}
    )
    assert response.status_code == 200
    return {d["filename"]: d["generating"] for d in response.json()}


def test_a_document_with_a_queued_or_running_generation_is_flagged(world):
    _add_document(world["course"], "queued.txt", JobStatus.queued)
    _add_document(world["course"], "running.txt", JobStatus.running)

    flags = _generating_by_filename(world)

    assert flags == {"queued.txt": True, "running.txt": True}


def test_finished_failed_or_absent_generations_are_not_flagged(world):
    _add_document(world["course"], "done.txt", JobStatus.done)
    _add_document(world["course"], "failed.txt", JobStatus.failed)
    _add_document(world["course"], "never.txt", None)

    flags = _generating_by_filename(world)

    assert flags == {"done.txt": False, "failed.txt": False, "never.txt": False}


def test_a_running_document_pipeline_job_is_not_a_generation(world):
    _add_document(world["course"], "pipeline.txt", JobStatus.running, job_type=JobType.document_pipeline)

    assert _generating_by_filename(world) == {"pipeline.txt": False}
