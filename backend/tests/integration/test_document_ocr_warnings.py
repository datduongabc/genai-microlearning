"""Warnings live on the document after the extraction job's payload is discarded."""

import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.ingestion.document_pipeline import run_document_pipeline_step
from app.jobs.queue import enqueue_job
from app.main import app
from app.models import Course, Document, DocumentStatus, Job, JobType, User


@pytest.fixture
def world(tmp_path, monkeypatch):
    def broken(img, lang):
        raise RuntimeError("OCR test failure")

    monkeypatch.setattr("app.ingestion.document_readers.pytesseract.image_to_string", broken)
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
                      payload={"storage_path": str(world["path"])})
    db.commit()
    ids = doc.id, job.id
    db.close()
    return ids


def _step(job_id):
    db = SessionLocal()
    try:
        run_document_pipeline_step(db.get(Job, job_id))
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


def test_a_completely_unreadable_document_keeps_warnings_before_failing(world):
    _doc_id, job_id = _upload(world, "")
    with pytest.raises(ValueError, match="Không đọc được nội dung"):
        _step(job_id)
    warnings = _documents(world)[0]["extraction_warnings"]
    assert [warning["page"] for warning in warnings] == [1, 2]
