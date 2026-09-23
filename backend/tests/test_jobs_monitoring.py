import uuid

from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Course, Document, Job, JobStatus, JobType, User, UserRole

client = TestClient(app)


def _make_authed_user() -> str:
    """Direct DB user + minted token, bypassing the rate-limited /auth/*
    endpoints -- these tests are about the monitoring endpoint, not auth."""
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        user = User(
            email=f"jobs-{suffix}@example.com", name="Test", hashed_password="x", role=UserRole.teacher
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return create_access_token(user.id), user.id
    finally:
        db.close()


def _make_job(owner_id, job_type: JobType, status: JobStatus, step: str, last_error: str | None) -> None:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        course = Course(name=f"Course {suffix}", owner_id=owner_id)
        db.add(course)
        db.flush()
        document = Document(course_id=course.id, filename=f"file-{suffix}.pdf")
        db.add(document)
        db.flush()
        job = Job(
            job_type=job_type, document_id=document.id, step=step, status=status,
            attempts=2, max_attempts=5, last_error=last_error,
        )
        db.add(job)
        db.commit()
    finally:
        db.close()


def test_lists_failed_jobs_with_document_and_course_context():
    token, user_id = _make_authed_user()
    _make_job(user_id, JobType.document_pipeline, JobStatus.failed, "extract", "boom: bad pdf")

    response = client.get("/jobs/failed", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) == 1
    assert jobs[0]["step"] == "extract"
    assert jobs[0]["last_error"] == "boom: bad pdf"
    assert jobs[0]["document_filename"].endswith(".pdf")
    assert jobs[0]["course_name"] is not None


def test_does_not_show_jobs_from_a_different_users_courses():
    token_a, _user_a = _make_authed_user()
    _, user_b = _make_authed_user()
    _make_job(user_b, JobType.document_pipeline, JobStatus.failed, "extract", "someone else's failure")

    response = client.get("/jobs/failed", headers={"Authorization": f"Bearer {token_a}"})
    assert response.status_code == 200
    assert response.json() == []


def test_excludes_non_failed_jobs():
    token, user_id = _make_authed_user()
    _make_job(user_id, JobType.document_pipeline, JobStatus.done, "extract", None)
    _make_job(user_id, JobType.document_pipeline, JobStatus.running, "chunk", None)

    response = client.get("/jobs/failed", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == []


def test_filters_by_job_type():
    token, user_id = _make_authed_user()
    _make_job(user_id, JobType.document_pipeline, JobStatus.failed, "extract", "doc error")
    _make_job(user_id, JobType.module_generation, JobStatus.failed, "generate", "gen error")

    response = client.get(
        "/jobs/failed", params={"job_type": "module_generation"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) == 1
    assert jobs[0]["last_error"] == "gen error"
