"""Chạy thật 3 step (extract -> chunk -> embed) trên 1 file .html thật --
không cần LLM/API ngoài; fixture offline_embeddings trong tests/conftest.py
thay provider embedding, còn các bước pipeline và lưu DB chạy thật.

Dọn Job/Document/Course/User tạo ra ở cuối mỗi test -- claim_next_job()
(test_job_queue.py) quét TOÀN BỘ bảng jobs không lọc theo document/course,
1 job 'queued' sót lại (test dừng giữa chừng, không đi qua handle_job_error)
đủ để làm sai 1 test claim khác chạy sau (đã thấy thật)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db import SessionLocal
from app.ingestion.document_pipeline import run_document_pipeline_step
from app.jobs.queue import enqueue_job
from app.models import (
    Chunk,
    Course,
    Document,
    DocumentStatus,
    Job,
    JobStatus,
    JobType,
    User,
)
from tests.job_helpers import claimed

SAMPLE_TEXT = (
    "<p>Doan mot noi ve khai niem A voi nhieu chi tiet quan trong can duoc giu "
    "nguyen ven khi chia nho tai lieu thanh cac phan.</p>"
    "<p>Doan hai noi ve khai niem B, khac voi khai niem A o mot so diem chinh "
    "can duoc trich xuat dung.</p>"
)


@pytest.fixture
def seed_document_with_file(tmp_path):
    created_doc_ids: list[uuid.UUID] = []

    def _seed(db):
        user = User(email=f"test-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Document pipeline test", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="sample.html", status=DocumentStatus.uploaded)
        db.add(doc)
        db.commit()
        created_doc_ids.append(doc.id)

        file_path = tmp_path / "sample.html"
        file_path.write_text(SAMPLE_TEXT, encoding="utf-8")
        return doc, str(file_path)

    yield _seed

    db = SessionLocal()
    try:
        for doc_id in created_doc_ids:
            doc = db.get(Document, doc_id)
            if doc is None:
                continue
            course_id = doc.course_id
            db.query(Job).filter(Job.document_id == doc_id).delete()
            db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
            db.query(Document).filter(Document.id == doc_id).delete()
            course = db.get(Course, course_id)
            if course is not None:
                owner_id = course.owner_id
                db.query(Course).filter(Course.id == course_id).delete()
                db.query(User).filter(User.id == owner_id).delete()
        db.commit()
    finally:
        db.close()


def test_document_pipeline_extract_chunk_embed_end_to_end(seed_document_with_file):
    db = SessionLocal()
    try:
        doc, storage_path = seed_document_with_file(db)
        job = enqueue_job(
            db, job_type=JobType.document_pipeline, step="extract",
            document_id=doc.id, payload={"storage_path": storage_path},
        )
        # As if the extract had taken an hour (a long video): the next step is due from when it is
        # queued, or the oldest-ready-job metric counts that hour as waiting for a worker.
        job.run_after = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
        job_id = job.id
        doc_id = doc.id
    finally:
        db.close()

    # extract
    db = SessionLocal()
    try:
        job = claimed(db, job_id)
        run_document_pipeline_step(job)
    finally:
        db.close()

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        doc = db.get(Document, doc_id)
        assert job.step == "chunk"
        assert job.status == JobStatus.queued
        assert job.run_after > datetime.now(UTC) - timedelta(minutes=1)
        assert "units" in job.payload
        assert doc.raw_text and "khai niem A" in doc.raw_text
    finally:
        db.close()

    # chunk
    db = SessionLocal()
    try:
        job = claimed(db, job_id)
        run_document_pipeline_step(job)
    finally:
        db.close()

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job.step == "embed"
        assert job.status == JobStatus.queued
        assert job.run_after > datetime.now(UTC) - timedelta(minutes=1)
        assert "chunks" in job.payload
        assert "units" not in job.payload
        # Tên tài liệu đi vào dòng ngữ cảnh của mọi chunk, để truy hồi trên cả môn học biết
        # đoạn nào thuộc bài nào.
        assert all(c["content"].startswith("sample") for c in job.payload["chunks"])
    finally:
        db.close()

    # embed -- real local embedding model, no external API/quota.
    db = SessionLocal()
    try:
        job = claimed(db, job_id)
        run_document_pipeline_step(job)
    finally:
        db.close()

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        doc = db.get(Document, doc_id)
        assert job.status == JobStatus.done
        # the chunk text now lives in the chunks table; keeping a second copy
        # in the finished job would only grow the jobs table
        assert job.payload == {}
        assert doc.status == DocumentStatus.embedded
        chunks = db.query(Chunk).filter(Chunk.document_id == doc_id).all()
        assert len(chunks) >= 1
        assert all(c.embedding is not None for c in chunks)
    finally:
        db.close()


def test_document_pipeline_extract_fails_cleanly_on_unsupported_content(seed_document_with_file):
    """chunk step voi payload rong (khong co 'units') phai raise ValueError ro
    rang thay vi loi mo ho -- dung duoc handle_job_error() phan loai terminal."""
    db = SessionLocal()
    try:
        doc, _ = seed_document_with_file(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="chunk", document_id=doc.id, payload={})
        db.commit()
        job_id = job.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        job = claimed(db, job_id)
        try:
            run_document_pipeline_step(job)
            raised = False
        except ValueError:
            raised = True
        assert raised
    finally:
        db.close()
