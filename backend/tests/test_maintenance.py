"""Periodic clean-up of data that only ever grows: finished jobs and refresh
tokens that can no longer be used."""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.db import SessionLocal
from app.jobs import queue as job_queue
from app.jobs.maintenance import purge_old_records
from app.models import Job, JobStatus, JobType, RefreshToken, User, UserRole


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture(autouse=True)
def retention(monkeypatch):
    monkeypatch.setattr(settings, "done_job_retention_days", 7)
    monkeypatch.setattr(settings, "failed_job_retention_days", 30)


@pytest.fixture
def made():
    """Creates jobs/tokens and removes every one of them afterwards, so a
    row left over from a failing test cannot confuse the job-queue tests
    (claim_next_job() scans the whole jobs table)."""
    job_ids: list[uuid.UUID] = []
    token_ids: list[uuid.UUID] = []
    user_ids: list[uuid.UUID] = []

    class Made:
        @staticmethod
        def job(status: JobStatus, age_days: float, job_type=JobType.document_pipeline) -> uuid.UUID:
            db = SessionLocal()
            try:
                job = Job(job_type=job_type, step="embed", status=status, created_at=_now() - timedelta(days=age_days))
                db.add(job)
                db.commit()
                job_ids.append(job.id)
                return job.id
            finally:
                db.close()

        @staticmethod
        def user() -> uuid.UUID:
            db = SessionLocal()
            try:
                user = User(
                    email=f"maint-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x",
                    role=UserRole.teacher,
                )
                db.add(user)
                db.commit()
                user_ids.append(user.id)
                return user.id
            finally:
                db.close()

        @staticmethod
        def token(user_id, expires_in_days: float, revoked_days_ago: float | None = None) -> uuid.UUID:
            db = SessionLocal()
            try:
                token = RefreshToken(
                    user_id=user_id,
                    token_hash=uuid.uuid4().hex,
                    expires_at=_now() + timedelta(days=expires_in_days),
                    revoked_at=None if revoked_days_ago is None else _now() - timedelta(days=revoked_days_ago),
                )
                db.add(token)
                db.commit()
                token_ids.append(token.id)
                return token.id
            finally:
                db.close()

    yield Made

    db = SessionLocal()
    try:
        db.query(Job).filter(Job.id.in_(job_ids)).delete(synchronize_session=False)
        db.query(RefreshToken).filter(RefreshToken.id.in_(token_ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _exists(model, row_id) -> bool:
    db = SessionLocal()
    try:
        return db.get(model, row_id) is not None
    finally:
        db.close()


def test_old_finished_jobs_are_removed_and_recent_ones_kept(made):
    old_done = made.job(JobStatus.done, age_days=8)
    recent_done = made.job(JobStatus.done, age_days=1)

    purge_old_records()

    assert not _exists(Job, old_done)
    assert _exists(Job, recent_done)


def test_failed_jobs_are_kept_longer_than_finished_ones(made):
    failed_but_recent_enough = made.job(JobStatus.failed, age_days=10)  # past the 7-day mark for done jobs
    failed_long_ago = made.job(JobStatus.failed, age_days=31)

    purge_old_records()

    assert _exists(Job, failed_but_recent_enough)
    assert not _exists(Job, failed_long_ago)


@pytest.mark.parametrize("status", [JobStatus.queued, JobStatus.running])
def test_unfinished_jobs_are_never_removed_however_old(made, status):
    stale = made.job(status, age_days=90)

    purge_old_records()

    assert _exists(Job, stale)


def test_expired_refresh_tokens_are_removed_and_valid_ones_kept(made):
    user_id = made.user()
    expired = made.token(user_id, expires_in_days=-1)
    valid = made.token(user_id, expires_in_days=5)

    purge_old_records()

    assert not _exists(RefreshToken, expired)
    assert _exists(RefreshToken, valid)


def test_long_revoked_refresh_tokens_are_removed_and_freshly_revoked_ones_kept(made):
    user_id = made.user()
    revoked_long_ago = made.token(user_id, expires_in_days=5, revoked_days_ago=3)
    revoked_just_now = made.token(user_id, expires_in_days=5, revoked_days_ago=0)

    purge_old_records()

    assert not _exists(RefreshToken, revoked_long_ago)
    assert _exists(RefreshToken, revoked_just_now)


def test_purge_reports_what_it_removed(made):
    made.job(JobStatus.done, age_days=8)
    user_id = made.user()
    made.token(user_id, expires_in_days=-1)

    removed = purge_old_records()

    assert removed["jobs"] >= 1
    assert removed["refresh_tokens"] >= 1


@pytest.fixture
def fast_worker(monkeypatch):
    """Runs the real worker loop against a stubbed queue: nothing is claimed,
    so no real job is touched, and every interval is shortened."""
    calls: list[str] = []
    monkeypatch.setattr(job_queue, "claim_next_job", lambda worker_id: None)
    monkeypatch.setattr(job_queue, "sweep_stuck_jobs", lambda: None)
    monkeypatch.setattr(job_queue, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(job_queue, "MAINTENANCE_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(job_queue, "MAINTENANCE_FIRST_RUN_DELAY_SECONDS", 0)

    def run(*, run_sweeper: bool, purge):
        monkeypatch.setattr(job_queue, "purge_old_records", purge)
        job_queue._stop_event.clear()
        thread = threading.Thread(
            target=job_queue._worker_loop, args=("test-worker",), kwargs={"run_sweeper": run_sweeper}, daemon=True
        )
        thread.start()
        time.sleep(0.6)
        job_queue._stop_event.set()
        thread.join(timeout=5)
        job_queue._stop_event.clear()
        return calls

    return run, calls


def test_the_worker_runs_maintenance_repeatedly(fast_worker):
    run, calls = fast_worker

    run(run_sweeper=True, purge=lambda: calls.append("purge"))

    assert len(calls) >= 2


def test_only_the_designated_worker_runs_maintenance(fast_worker):
    run, calls = fast_worker

    run(run_sweeper=False, purge=lambda: calls.append("purge"))

    assert calls == []


def test_a_failing_maintenance_run_does_not_stop_the_worker(fast_worker):
    run, calls = fast_worker

    def exploding_purge():
        calls.append("purge")
        raise RuntimeError("database hiccup")

    run(run_sweeper=True, purge=exploding_purge)

    assert len(calls) >= 2  # it kept running and tried again
