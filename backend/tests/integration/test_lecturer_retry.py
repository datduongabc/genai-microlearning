"""A lecturer sees their own failures on the document and retries them from their own screen:
the same job runs again, with nothing uploaded again and no second module made. A failure also
stays visible after a reload, because the document list carries it."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.ingestion.document_pipeline import run_document_pipeline_step
from app.jobs.queue import handle_job_error
from app.main import app
from app.models import (
    Chunk,
    Document,
    DocumentStatus,
    Job,
    JobStatus,
    JobType,
    Module,
)
from tests.auth_helpers import create_and_login

client = TestClient(app)


@pytest.fixture
def lecturer():
    login = create_and_login(client, f"retry-{uuid.uuid4().hex[:10]}@example.com")
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    course = client.post("/courses", json={"name": "Môn học"}, headers=headers).json()["id"]
    return {"headers": headers, "course": uuid.UUID(course)}


def _document(course_id, status=DocumentStatus.embedded, filename="bai-giang.txt"):
    with SessionLocal() as db:
        doc = Document(course_id=course_id, filename=filename, status=status, raw_text="nội dung")
        db.add(doc)
        db.commit()
        return doc.id


def _job(document_id, job_type, status, *, error="lỗi", step=None, payload=None, created_at=None):
    with SessionLocal() as db:
        job = Job(
            job_type=job_type, document_id=document_id, status=status,
            step=step or ("extract" if job_type == JobType.document_pipeline else "generate"),
            attempts=5, last_error=error, payload=payload or {},
        )
        if created_at is not None:
            job.created_at = created_at
        db.add(job)
        db.commit()
        return job.id




def _listed(lecturer):
    response = client.get("/documents", params={"course_id": str(lecturer["course"])}, headers=lecturer["headers"])
    assert response.status_code == 200
    return {d["filename"]: d for d in response.json()}


def test_a_failed_document_shows_its_error_and_the_job_to_retry(lecturer):
    doc = _document(lecturer["course"], DocumentStatus.failed, "hong.pdf")
    job = _job(doc, JobType.document_pipeline, JobStatus.failed, error="Không đọc được tệp PDF này")

    listed = _listed(lecturer)["hong.pdf"]

    assert listed["status"] == "failed"
    assert listed["failures"] == [{
        "job_id": str(job), "job_type": "document_pipeline",
        "error": "Không đọc được tệp PDF này",
    }]


def test_a_failed_generation_stays_on_its_document_after_a_reload(lecturer):
    doc = _document(lecturer["course"], filename="giao-trinh.pdf")
    job = _job(doc, JobType.module_generation, JobStatus.failed, error="Gateway hết hạn mức")

    first = _listed(lecturer)["giao-trinh.pdf"]
    reloaded = _listed(lecturer)["giao-trinh.pdf"]

    assert first["status"] == "embedded" and first["module_count"] == 0  # no module, and no trace but the job
    assert first["failures"] == reloaded["failures"]
    assert first["failures"][0]["job_id"] == str(job)
    assert first["failures"][0]["error"] == "Gateway hết hạn mức"


def test_the_error_is_cut_to_a_short_message(lecturer):
    doc = _document(lecturer["course"], filename="dai.pdf")
    _job(doc, JobType.module_generation, JobStatus.failed, error="x" * 500)

    assert len(_listed(lecturer)["dai.pdf"]["failures"][0]["error"]) == 200


def test_only_failed_jobs_show_and_a_document_without_failures_has_an_empty_list(lecturer):
    doc = _document(lecturer["course"], filename="ok.pdf")
    for status in (JobStatus.done, JobStatus.running, JobStatus.queued):
        _job(doc, JobType.module_generation, status)

    assert _listed(lecturer)["ok.pdf"]["failures"] == []


def test_a_failure_that_was_generated_again_is_not_shown(lecturer):
    doc = _document(lecturer["course"], filename="again.pdf")
    _job(doc, JobType.module_generation, JobStatus.failed,
         created_at=datetime.now(UTC) - timedelta(hours=1))
    _job(doc, JobType.module_generation, JobStatus.running)

    assert _listed(lecturer)["again.pdf"]["failures"] == []


def test_retry_runs_the_same_failed_generation_again_and_makes_no_extra_module(lecturer):
    doc = _document(lecturer["course"], filename="gen.pdf")
    job = _job(doc, JobType.module_generation, JobStatus.failed, payload={"self_check": True})

    response = client.post(f"/jobs/{job}/retry", headers=lecturer["headers"])

    assert response.status_code == 200 and response.json()["status"] == "queued"
    with SessionLocal() as db:
        again = db.get(Job, job)
        assert again.status == JobStatus.queued and again.attempts == 0
        assert again.payload == {"self_check": True}
        assert db.query(Job).filter(Job.document_id == doc).count() == 1  # the same job, not a new one
        assert db.query(Module).filter(Module.document_id == doc).count() == 0
    assert _listed(lecturer)["gen.pdf"]["failures"] == []


def test_retrying_a_failed_document_shows_it_processing_again(lecturer):
    doc = _document(lecturer["course"], DocumentStatus.failed, "hong.pdf")
    job = _job(doc, JobType.document_pipeline, JobStatus.failed, step="chunk")

    assert client.post(f"/jobs/{job}/retry", headers=lecturer["headers"]).status_code == 200

    listed = _listed(lecturer)["hong.pdf"]
    assert listed["status"] == "processing" and listed["failures"] == []
    with SessionLocal() as db:
        assert db.get(Job, job).step == "chunk"  # from where it stopped


def test_retrying_a_document_that_failed_part_way_finishes_it_without_uploading_again(lecturer, tmp_path):
    path = tmp_path / "bai.txt"
    path.write_text("Khái niệm A rất quan trọng.\n\nKhái niệm B khác khái niệm A.", encoding="utf-8")
    with SessionLocal() as db:
        doc = Document(course_id=lecturer["course"], filename="bai.txt", status=DocumentStatus.uploaded)
        db.add(doc)
        db.flush()
        job = Job(job_type=JobType.document_pipeline, document_id=doc.id, step="extract", payload={"storage_path": str(path)})
        db.add(job)
        db.commit()
        doc_id, job_id = doc.id, job.id

    # The provider gave out for good: the job is failed and the document says so.
    handle_job_error(job_id, ValueError("hết hạn mức"))
    assert _listed(lecturer)["bai.txt"]["status"] == "failed"

    assert client.post(f"/jobs/{job_id}/retry", headers=lecturer["headers"]).status_code == 200
    for _ in range(3):
        with SessionLocal() as db:
            current = db.get(Job, job_id)
            if current.status != JobStatus.queued:
                break
            current.status = JobStatus.running
            db.commit()
            db.refresh(current)
            db.expunge(current)
        run_document_pipeline_step(current)

    listed = _listed(lecturer)["bai.txt"]
    assert listed["status"] == "embedded" and listed["failures"] == []
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == JobStatus.done
        assert db.query(Job).filter(Job.document_id == doc_id).count() == 1
        assert db.query(Chunk).filter(Chunk.document_id == doc_id).count() >= 1


def test_something_that_is_not_failed_cannot_be_retried(lecturer):
    doc = _document(lecturer["course"])
    running = _job(doc, JobType.module_generation, JobStatus.running)
    done = _job(doc, JobType.module_generation, JobStatus.done)

    for job in (running, done):
        assert client.post(f"/jobs/{job}/retry", headers=lecturer["headers"]).status_code == 409


def test_a_failure_that_was_generated_again_cannot_be_retried_into_a_duplicate(lecturer):
    doc = _document(lecturer["course"])
    old = _job(doc, JobType.module_generation, JobStatus.failed,
               created_at=datetime.now(UTC) - timedelta(hours=1))
    _job(doc, JobType.module_generation, JobStatus.queued)

    assert client.post(f"/jobs/{old}/retry", headers=lecturer["headers"]).status_code == 409


def test_retrying_twice_is_refused_the_second_time(lecturer):
    doc = _document(lecturer["course"])
    job = _job(doc, JobType.module_generation, JobStatus.failed)

    assert client.post(f"/jobs/{job}/retry", headers=lecturer["headers"]).status_code == 200
    assert client.post(f"/jobs/{job}/retry", headers=lecturer["headers"]).status_code == 409


def test_another_lecturers_job_is_not_found_like_one_that_does_not_exist(lecturer):
    doc = _document(lecturer["course"])
    job = _job(doc, JobType.module_generation, JobStatus.failed)
    other = create_and_login(client, f"stranger-{uuid.uuid4().hex[:8]}@example.com")
    headers = {"Authorization": f"Bearer {other['access_token']}"}

    real = client.post(f"/jobs/{job}/retry", headers=headers)
    ghost = client.post(f"/jobs/{uuid.uuid4()}/retry", headers=headers)

    assert (real.status_code, real.json()) == (ghost.status_code, ghost.json()) == (404, real.json())
    with SessionLocal() as db:
        assert db.get(Job, job).status == JobStatus.failed


def test_the_failures_of_another_lecturer_are_not_in_my_list(lecturer):
    doc = _document(lecturer["course"], filename="mine.pdf")
    _job(doc, JobType.module_generation, JobStatus.failed, error="secret error")
    other = create_and_login(client, f"stranger-{uuid.uuid4().hex[:8]}@example.com")
    headers = {"Authorization": f"Bearer {other['access_token']}"}

    response = client.get("/documents", params={"course_id": str(lecturer["course"])}, headers=headers)

    assert response.status_code == 404
