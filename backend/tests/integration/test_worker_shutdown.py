"""A worker that stops, on a deploy or a scale-in, gives its unfinished jobs back at once instead
of leaving them as running for the sweeper's threshold."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.db import SessionLocal
from app.ingestion import document_readers
from app.jobs import queue
from app.models import Course, Document, DocumentStatus, Job, JobStatus, JobType, User


@pytest.fixture
def document():
    db = SessionLocal()
    try:
        user = User(email=f"stop-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Worker stop", owner_id=user.id)
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


def _job(document_id, *, status, locked_by=None, attempts=1):
    db = SessionLocal()
    try:
        job = Job(job_type=JobType.document_pipeline, step="extract", document_id=document_id, payload={},
                  status=status, locked_by=locked_by, attempts=attempts,
                  locked_at=datetime.now(UTC) if locked_by else None,
                  run_after=datetime.now(UTC) - timedelta(minutes=1))
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


def _get(job_id):
    db = SessionLocal()
    try:
        return db.get(Job, job_id)
    finally:
        db.close()


def test_only_the_stopping_workers_jobs_go_back_and_the_attempt_is_not_counted(document):
    mine = _job(document, status=JobStatus.running, locked_by="worker-mine01", attempts=2)
    other = _job(document, status=JobStatus.running, locked_by="worker-other1")

    assert queue.release_jobs_of(["worker-mine01"]) == 1

    released = _get(mine)
    assert (released.status, released.locked_by, released.attempts) == (JobStatus.queued, None, 2)
    assert _get(other).status == JobStatus.running


def test_stopping_with_a_job_still_running_puts_it_back(document, monkeypatch):
    job_id = _job(document, status=JobStatus.queued)
    started, release = threading.Event(), threading.Event()

    def slow_step(job):
        started.set()
        release.wait(10)

    # stop_worker shuts the shared transcription pool down; this test must not take it from the others.
    monkeypatch.setattr(document_readers, "_ASR_EXECUTOR", ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(settings, "worker_pool_size", 1)
    monkeypatch.setattr(settings, "queue_metrics", False)
    monkeypatch.setattr(settings, "worker_shutdown_grace_seconds", 0.2)
    monkeypatch.setattr(queue, "execute_job_step", slow_step)
    monkeypatch.setattr(queue, "claim_next_job", lambda worker_id: _claim_only(job_id, worker_id))
    try:
        queue.start_worker()
        assert started.wait(10)
        assert queue.has_active_jobs()
        queue.stop_worker()
        assert _get(job_id).status == JobStatus.queued
    finally:
        release.set()
        for t in queue._worker_threads:
            t.join(timeout=5)


def _claim_only(job_id, worker_id):
    """claim_next_job for this test's job alone: other tests may leave due jobs in the table."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id, with_for_update=True)
        if job is None or job.status != JobStatus.queued:
            return None
        job.status, job.locked_by, job.locked_at = JobStatus.running, worker_id, datetime.now(UTC)
        db.commit()
        db.refresh(job)
        db.expunge(job)
        return job
    finally:
        db.close()
