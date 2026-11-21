"""/ready tells the load balancer whether a task can serve, and the queue counts are what the
worker service scales on; both must be right against the real database."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import SessionLocal, WorkerSessionLocal
from app.jobs.metrics import queue_counts
from app.models import Course, Document, DocumentStatus, Job, JobStatus, JobType, User

client = TestClient(main.app)


def test_ready_answers_ok_while_the_database_answers():
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_ready_answers_503_when_the_database_does_not(monkeypatch):
    monkeypatch.setattr(main, "database_ready", lambda: False)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["database"] == "down"


@pytest.fixture
def document():
    db = SessionLocal()
    try:
        user = User(email=f"metrics-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Queue metrics", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.html", status=DocumentStatus.uploaded)
        db.add(doc)
        db.commit()
        ids = (user.id, course.id, doc.id)
    finally:
        db.close()
    yield ids[2]
    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids[1]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids[0]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _counts():
    db = WorkerSessionLocal()
    try:
        return queue_counts(db)
    finally:
        db.close()


def test_queue_counts_leave_out_jobs_waiting_out_a_backoff(document):
    # The counts cover the whole table, and other tests may leave jobs in it, so this compares.
    before = _counts()
    now = datetime.now(UTC)
    db = SessionLocal()
    try:
        def job(status, run_after):
            db.add(Job(job_type=JobType.document_pipeline, step="extract", document_id=document,
                       status=status, run_after=run_after, payload={}))

        job(JobStatus.queued, now - timedelta(days=30))
        job(JobStatus.queued, now - timedelta(minutes=1))
        job(JobStatus.queued, now + timedelta(minutes=10))  # backing off: no worker could take it
        job(JobStatus.running, now - timedelta(minutes=2))
        job(JobStatus.done, now - timedelta(minutes=30))
        db.commit()
    finally:
        db.close()

    after = _counts()

    assert after["JobsReady"] - before["JobsReady"] == 2
    assert after["RenderJobsReady"] == before["RenderJobsReady"]
    assert after["JobsRunning"] - before["JobsRunning"] == 1
    assert abs(after["OldestReadyJobAgeSeconds"] - 30 * 86400) < 60


def test_a_database_that_accepts_and_never_answers_is_not_ready_by_the_deadline(monkeypatch):
    # Seen with the database container paused: the socket stays open and nothing answers, so
    # neither connect_timeout nor statement_timeout ends the check. It hung for 87 s before.
    import threading
    import time

    from app import db

    release = threading.Event()
    started = []

    def hang():
        started.append(1)
        release.wait(10)

    monkeypatch.setattr(db, "READINESS_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(db, "_select_one", hang)
    try:
        begin = time.monotonic()
        assert db.database_ready() is False
        assert time.monotonic() - begin < 1
        assert db.database_ready() is False
        assert len(started) == 1, "a hanging check is waited on, not joined by a second one"
    finally:
        release.set()
        time.sleep(0.05)
    monkeypatch.undo()
    assert db.database_ready() is True


def test_a_due_render_is_counted_for_the_render_service_not_the_general_workers(document):
    before = _counts()
    db = SessionLocal()
    try:
        db.add(Job(job_type=JobType.video_render, step="render", document_id=document, status=JobStatus.queued,
                   run_after=datetime.now(UTC) - timedelta(seconds=5), payload={}))
        db.commit()
    finally:
        db.close()

    after = _counts()

    assert (after["RenderJobsReady"] - before["RenderJobsReady"], after["JobsReady"] - before["JobsReady"]) == (1, 0)
