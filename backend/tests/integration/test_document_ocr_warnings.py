"""Warnings live on the document after the extraction job's payload is discarded."""

import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.ingestion.document_pipeline import run_document_pipeline_step
from app.jobs.queue import enqueue_job, execute_job_step, handle_job_error
from app.main import app
from app.models import Course, Document, DocumentStatus, Job, JobType, User
from tests.job_helpers import claimed


@pytest.fixture
def world(tmp_path, monkeypatch):
    def broken(img, lang):
        raise RuntimeError("OCR test failure")

    monkeypatch.setattr("app.ingestion.document_readers.pytesseract.image_to_string", broken)
    monkeypatch.setattr("app.config.settings.upload_storage_dir", str(tmp_path))
    db = SessionLocal()
    user = User(email=f"ocr-{uuid.uuid4().hex}@example.com", name="OCR", hashed_password="x")
    db.add(user)
    db.flush()
    course = Course(name="OCR warnings", owner_id=user.id)
    db.add(course)
    db.commit()
    ids = {"owner": user.id, "course": course.id}
    db.close()
    yield {**ids, "path": tmp_path / "scan.pdf"}
    db = SessionLocal()
    db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
    db.query(User).filter(User.id == ids["owner"]).delete(synchronize_session=False)
    db.commit()
    db.close()


def _upload(world, text):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        if text:
            page.insert_text((50, 80), text)
        pdf.new_page()
        pdf.save(world["path"])
    db = SessionLocal()
    doc = Document(course_id=world["course"], filename="scan.pdf", status=DocumentStatus.uploaded)
    db.add(doc)
    db.flush()
    job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract", document_id=doc.id,
                      payload={"storage_key": world["path"].name})
    db.commit()
    ids = doc.id, job.id
    db.close()
    return ids


def _step(job_id):
    db = SessionLocal()
    try:
        run_document_pipeline_step(claimed(db, job_id))
    finally:
        db.close()


def _documents(world):
    response = TestClient(app).get("/documents", params={"course_id": world["course"]},
                                   headers={"Authorization": f"Bearer {create_access_token(world['owner'])}"})
    assert response.status_code == 200
    return response.json()


def test_warning_survives_successful_embedding_and_a_fresh_document_list(world):
    doc_id, job_id = _upload(world, "The ready queue contains processes waiting for the CPU.")
    for _ in range(3):
        _step(job_id)
    db = SessionLocal()
    try:
        assert db.get(Job, job_id).payload == {}
        assert db.get(Document, doc_id).status == DocumentStatus.embedded
    finally:
        db.close()
    doc = _documents(world)[0]
    assert doc["extraction_warnings"][0]["page"] == 2
    assert doc["extraction_warnings"][0]["code"] == "ocr_failed"
    assert doc["failures"] == []


def test_a_completely_unreadable_document_keeps_warnings_and_shows_why_it_failed(world):
    _doc_id, job_id = _upload(world, "")
    db = SessionLocal()
    try:
        # What the worker loop does with a step that raises.
        with pytest.raises(ValueError) as failure:
            execute_job_step(claimed(db, job_id))
    finally:
        db.close()
    handle_job_error(job_id, failure.value)

    doc = _documents(world)[0]
    assert doc["status"] == "failed"
    assert [warning["page"] for warning in doc["extraction_warnings"]] == [1, 2]
    assert [f["error"] for f in doc["failures"]] == [
        "Không đọc được nội dung tài liệu; hãy tải lại bản có lớp chữ."
    ]
