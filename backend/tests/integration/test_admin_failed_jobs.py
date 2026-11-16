"""The admin's view of failed jobs: the failures of every lecturer, with the file name and course
name beside them and nothing of what was uploaded or generated, and the Retry action on one or
several of them."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import (
    Course,
    Document,
    DocumentStatus,
    FlashcardSet,
    Job,
    JobStatus,
    JobType,
    MediaStatus,
    Module,
    User,
)
from tests.auth_helpers import admin_headers, make_account

client = TestClient(app)


@pytest.fixture(scope="module")
def admin():
    return admin_headers()


def _lecturer() -> uuid.UUID:
    return make_account(f"jobs-{uuid.uuid4().hex[:10]}@example.com").id


def _failed(owner_id, job_type=JobType.document_pipeline, *, error="boom", status=JobStatus.failed,
            created_at=None, document=None):
    """A document of `owner_id` with one job. Returns (job id, document id)."""
    with SessionLocal() as db:
        suffix = uuid.uuid4().hex[:8]
        if document is None:
            course = Course(name=f"Course {suffix}", owner_id=owner_id)
            db.add(course)
            db.flush()
            document = Document(
                course_id=course.id, filename=f"file-{suffix}.pdf", raw_text="SECRET TEXT OF THE DOCUMENT",
                status=DocumentStatus.failed if job_type == JobType.document_pipeline else DocumentStatus.embedded,
            )
            db.add(document)
            db.flush()
            document_id = document.id
        else:
            document_id = document
        job = Job(
            job_type=job_type, document_id=document_id,
            step="extract" if job_type == JobType.document_pipeline else "generate",
            status=status, attempts=5, max_attempts=5, last_error=error,
            payload={"units": ["SECRET PAYLOAD"]},
        )
        if created_at is not None:
            job.created_at = created_at
        db.add(job)
        db.commit()
        return job.id, document_id


def _failed_ids(admin, **params):
    response = client.get("/admin/jobs/failed", params=params, headers=admin)
    assert response.status_code == 200
    return {j["id"]: j for j in response.json()}


def test_lists_the_failed_jobs_of_every_lecturer_with_file_and_course_name(admin):
    first, second = _lecturer(), _lecturer()
    job_a, _ = _failed(first, error="boom: bad pdf")
    job_b, _ = _failed(second, JobType.module_generation)

    jobs = _failed_ids(admin)

    assert str(job_a) in jobs and str(job_b) in jobs
    row = jobs[str(job_a)]
    assert row["step"] == "extract" and row["last_error"] == "boom: bad pdf"
    assert row["document_filename"].endswith(".pdf") and row["course_name"].startswith("Course ")
    assert row["owner_email"].startswith("jobs-")
    assert {"attempts", "max_attempts", "created_at", "job_type", "module_id"} <= set(row)


def test_never_shows_what_was_uploaded_or_generated(admin):
    job, _ = _failed(_lecturer())

    response = client.get("/admin/jobs/failed", headers=admin)

    assert "SECRET" not in response.text
    assert not {"payload", "raw_text", "content", "chunks"} & set(_failed_ids(admin)[str(job)])


def test_excludes_jobs_that_are_not_failed(admin):
    owner = _lecturer()
    done, _ = _failed(owner, status=JobStatus.done)
    running, _ = _failed(owner, status=JobStatus.running)
    queued, _ = _failed(owner, status=JobStatus.queued)

    jobs = _failed_ids(admin)

    assert not {str(done), str(running), str(queued)} & set(jobs)


def test_filters_by_job_type(admin):
    owner = _lecturer()
    doc_job, _ = _failed(owner, JobType.document_pipeline)
    gen_job, _ = _failed(owner, JobType.module_generation)

    jobs = _failed_ids(admin, job_type="module_generation")

    assert str(gen_job) in jobs and str(doc_job) not in jobs


def test_counts_every_open_failure_per_type_beyond_the_list_cap(admin, monkeypatch):
    monkeypatch.setattr("app.api.admin.MAX_FAILED_JOBS", 1)
    before = client.get("/admin/counts", headers=admin).json()["failed_jobs"]
    owner = _lecturer()
    _failed(owner, JobType.document_pipeline)
    _failed(owner, JobType.document_pipeline)
    _failed(owner, JobType.module_generation)
    _failed(owner, JobType.module_generation, status=JobStatus.done)

    after = client.get("/admin/counts", headers=admin).json()["failed_jobs"]

    assert len(_failed_ids(admin)) == 1
    assert after.get("document_pipeline", 0) - before.get("document_pipeline", 0) == 2
    assert after.get("module_generation", 0) - before.get("module_generation", 0) == 1


def test_a_failure_that_was_generated_again_is_no_longer_listed(admin):
    owner = _lecturer()
    old, document = _failed(owner, JobType.module_generation, created_at=datetime.now(UTC) - timedelta(hours=1))
    newer, _ = _failed(owner, JobType.module_generation, status=JobStatus.running, document=document)
    jobs = _failed_ids(admin)

    assert str(old) not in jobs and str(newer) not in jobs


def test_retry_puts_the_picked_jobs_back_in_the_queue_from_where_they_stopped(admin):
    owner = _lecturer()
    first, doc_first = _failed(owner)
    second, _ = _failed(_lecturer(), JobType.module_generation)
    untouched, _ = _failed(owner)

    response = client.post("/admin/jobs/retry", json={"job_ids": [str(first), str(second)]}, headers=admin)

    assert response.status_code == 200
    assert set(response.json()["retried"]) == {str(first), str(second)}
    assert response.json()["skipped"] == []
    with SessionLocal() as db:
        for job_id in (first, second):
            job = db.get(Job, job_id)
            assert job.status == JobStatus.queued and job.attempts == 0
            assert job.locked_by is None and job.run_after <= datetime.now(UTC) + timedelta(seconds=5)
        assert db.get(Job, first).step == "extract"  # where it stopped, not from the start
        assert db.get(Job, first).payload == {"units": ["SECRET PAYLOAD"]}  # nothing uploaded again
        assert db.get(Job, untouched).status == JobStatus.failed
        assert db.get(Document, doc_first).status == DocumentStatus.processing  # shows as running again
        assert db.query(Module).filter(Module.document_id == doc_first).count() == 0
    assert str(first) not in _failed_ids(admin)


def test_retry_skips_what_is_not_failed_or_does_not_exist_and_retries_the_rest(admin):
    owner = _lecturer()
    failed, _ = _failed(owner)
    done, _ = _failed(owner, status=JobStatus.done)
    ghost = uuid.uuid4()

    response = client.post("/admin/jobs/retry", json={"job_ids": [str(failed), str(done), str(ghost)]}, headers=admin)

    assert response.status_code == 200
    assert response.json()["retried"] == [str(failed)]
    assert set(response.json()["skipped"]) == {str(done), str(ghost)}
    with SessionLocal() as db:
        assert db.get(Job, done).status == JobStatus.done


def test_an_admin_retrying_another_lecturers_failed_set_makes_it_generating_again(admin):
    # Row level security hides the set from the admin's own session; only the SECURITY DEFINER
    # function of migration 0036 can reset it.
    job_id, document = _failed(_lecturer(), JobType.flashcard_generation)
    with SessionLocal() as db:
        course_id = db.get(Document, document).course_id
        card_set = FlashcardSet(course_id=course_id, document_id=document, output_language="vi",
                                model_alias="gemini-flash", status=MediaStatus.failed, cards=[])
        db.add(card_set)
        db.flush()
        db.get(Job, job_id).payload = {"flashcard_set_id": str(card_set.id)}
        db.commit()
        set_id = card_set.id

    response = client.post("/admin/jobs/retry", json={"job_ids": [str(job_id)]}, headers=admin)

    assert response.json()["retried"] == [str(job_id)]
    with SessionLocal() as db:
        assert db.get(FlashcardSet, set_id).status == MediaStatus.generating


def test_retry_does_not_touch_a_failure_its_lecturer_already_generated_again(admin):
    owner = _lecturer()
    old, document = _failed(owner, JobType.module_generation, created_at=datetime.now(UTC) - timedelta(hours=1))
    _failed(owner, JobType.module_generation, status=JobStatus.queued, document=document)

    response = client.post("/admin/jobs/retry", json={"job_ids": [str(old)]}, headers=admin)

    assert response.json() == {"retried": [], "skipped": [str(old)], "reasons": {}}
    with SessionLocal() as db:
        assert db.get(Job, old).status == JobStatus.failed


def test_retrying_twice_runs_the_job_once(admin):
    job, _ = _failed(_lecturer())

    first = client.post("/admin/jobs/retry", json={"job_ids": [str(job)]}, headers=admin).json()
    second = client.post("/admin/jobs/retry", json={"job_ids": [str(job)]}, headers=admin).json()

    assert first["retried"] == [str(job)] and second["retried"] == []


def test_retry_needs_at_least_one_and_at_most_200_ids(admin):
    assert client.post("/admin/jobs/retry", json={"job_ids": []}, headers=admin).status_code == 422
    too_many = [str(uuid.uuid4()) for _ in range(201)]
    assert client.post("/admin/jobs/retry", json={"job_ids": too_many}, headers=admin).status_code == 422


def test_retry_is_logged(admin, caplog):
    import logging

    caplog.set_level(logging.INFO)
    job, _ = _failed(_lecturer())

    client.post("/admin/jobs/retry", json={"job_ids": [str(job)]}, headers=admin)

    assert any("admin_retry_jobs" in r.getMessage() and str(job) in r.getMessage() for r in caplog.records)


def test_the_lecturers_old_monitoring_route_is_gone():
    assert "/jobs/failed" not in app.openapi()["paths"]


def test_admin_accounts_are_not_lecturers_so_they_own_no_courses(admin):
    assert client.get("/courses", headers=admin).json() == []
    with SessionLocal() as db:
        assert db.query(User).filter(User.email.like("admin-%")).count() > 0
