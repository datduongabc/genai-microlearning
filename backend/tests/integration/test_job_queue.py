"""Test bản thân job_queue.py (claim/retry/backoff/sweep) -- không cần LLM
thật, không cần worker thread chạy nền (TestClient không dùng `with` nên
lifespan/start_worker() không tự chạy trong test, xem app/main.py -- gọi
claim_next_job()/handle_job_error() trực tiếp để test tất định).

claim_next_job() quét TOÀN BỘ bảng jobs (không lọc theo course/document như
các bảng khác) -- khác các test khác trong repo (không dọn Course/User vì
luôn query đúng ID cụ thể), Job còn sót lại từ 1 lần chạy test TRƯỚC thật sự
làm test SAU sai (đã thấy thật: claim_next_job() nhặt nhầm job cũ). Mọi test
ở đây phải dọn Job/Document/Course/User mình tạo ra ở cuối."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from litellm.exceptions import RateLimitError

from app.db import SessionLocal
from app.jobs.queue import (
    claim_next_job,
    enqueue_job,
    execute_job_step,
    handle_job_error,
    is_terminal_error,
    sweep_stuck_jobs,
)
from app.models import Course, Document, DocumentStatus, Job, JobStatus, JobType, User


@pytest.fixture
def seed_document():
    created_doc_ids: list[uuid.UUID] = []

    def _seed(db):
        user = User(email=f"test-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Job queue test", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="x.pdf", status=DocumentStatus.uploaded)
        db.add(doc)
        db.commit()
        created_doc_ids.append(doc.id)
        return doc

    yield _seed

    db = SessionLocal()
    try:
        for doc_id in created_doc_ids:
            doc = db.get(Document, doc_id)
            if doc is None:
                continue
            course_id = doc.course_id
            db.query(Job).filter(Job.document_id == doc_id).delete()
            db.query(Document).filter(Document.id == doc_id).delete()
            course = db.get(Course, course_id)
            if course is not None:
                owner_id = course.owner_id
                db.query(Course).filter(Course.id == course_id).delete()
                db.query(User).filter(User.id == owner_id).delete()
        db.commit()
    finally:
        db.close()


def test_is_terminal_error_classifies_correctly():
    assert is_terminal_error(ValueError("bad format")) is True
    assert is_terminal_error(KeyError("missing")) is True
    assert is_terminal_error(TypeError("wrong type")) is True
    assert is_terminal_error(RuntimeError("network hiccup")) is False
    assert is_terminal_error(RateLimitError("quota exceeded", llm_provider="gemini", model="test-model")) is False


@pytest.mark.parametrize("encrypted", [False, True], ids=["corrupted", "encrypted"])
def test_unreadable_pdf_fails_on_first_extract_attempt(seed_document, tmp_path, encrypted):
    import pymupdf

    path = tmp_path / "broken.pdf"
    if encrypted:
        with pymupdf.open() as pdf:
            pdf.new_page()
            pdf.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    else:
        path.write_bytes(b"%PDF-1.7\ncorrupted document")
    with SessionLocal() as db:
        doc = seed_document(db)
        doc_id = doc.id
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract",
                          document_id=doc_id, payload={"storage_path": str(path)})
        db.commit()
        job_id = job.id
        with pytest.raises(Exception) as failure:
            execute_job_step(job)
        handle_job_error(job_id, failure.value)
        db.expire_all()
        assert db.get(Job, job_id).status == JobStatus.failed
        assert db.get(Job, job_id).attempts == 1
        assert db.get(Job, job_id).last_error
        assert db.get(Document, doc_id).status == DocumentStatus.failed


def test_transient_embedder_failure_still_retries(seed_document, monkeypatch):
    from app.ingestion import document_pipeline

    def unavailable(texts):
        from sqlalchemy import text

        with SessionLocal() as probe:
            assert not probe.execute(text(
                "SELECT pid FROM pg_stat_activity WHERE datname = current_database() AND usename = 'app_worker' "
                "AND xact_start IS NOT NULL"
            )).all()
        raise RuntimeError("embedding gateway unavailable")

    monkeypatch.setattr(document_pipeline, "embed_texts", unavailable)
    with SessionLocal() as db:
        doc = seed_document(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="embed", document_id=doc.id,
                          payload={"chunks": [{"content": "lesson", "locator": {},
                                               "chunk_index": 0, "course_id": str(doc.course_id),
                                               "token_count": 1}]})
        db.commit()
        job_id = job.id
        with pytest.raises(RuntimeError, match="embedding gateway unavailable") as failure:
            execute_job_step(job)
        handle_job_error(job_id, failure.value)
        db.expire_all()
        after = db.get(Job, job_id)
        assert after.status == JobStatus.queued
        assert after.attempts == 1
        assert after.run_after > datetime.now(UTC)


def test_extract_releases_database_reads_before_transcription(seed_document, monkeypatch, tmp_path):
    from sqlalchemy import text

    from app.ingestion import document_pipeline
    from app.ingestion.document_readers import DocumentUnit

    def transcribe(path, **kwargs):
        with SessionLocal() as probe:
            assert not probe.execute(text(
                "SELECT pid FROM pg_stat_activity WHERE datname = current_database() AND usename = 'app_worker' "
                "AND xact_start IS NOT NULL"
            )).all()
        return [DocumentUnit(text="Transcript")]

    monkeypatch.setattr(document_pipeline, "read_document", transcribe)
    with SessionLocal() as db:
        doc = seed_document(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract", document_id=doc.id,
                          payload={"storage_path": str(tmp_path / "lecture.mp3")})
        db.commit()
        execute_job_step(job)
        db.refresh(job)
        assert job.step == "chunk"
        assert job.status == JobStatus.queued


def test_claim_next_job_only_returns_queued_and_due_jobs(seed_document):
    db = SessionLocal()
    try:
        doc = seed_document(db)
        my_job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract", document_id=doc.id)
        db.commit()
        my_job_id = my_job.id
    finally:
        db.close()

    # Bảng jobs là tài nguyên TOÀN CỤC (không lọc theo document/course như
    # các bảng khác trong repo) -- job khác (từ test khác/worker khác) có
    # thể đứng trước trong hàng đợi, nên không giả định "job claim được kế
    # tiếp chắc chắn là job của mình". Rút cạn hàng đợi tới khi thấy đúng
    # job vừa tạo (chặn ở 200 lần claim để không lặp vô hạn nếu có lỗi thật).
    worker_id = f"test-worker-{uuid.uuid4().hex[:6]}"
    found = None
    for _ in range(200):
        job = claim_next_job(worker_id)
        if job is None:
            break
        if job.id == my_job_id:
            found = job
            break
    assert found is not None, "job vừa enqueue phải claim được"
    assert found.status == JobStatus.running
    assert found.locked_by == worker_id


def test_claim_next_job_skips_future_run_after(seed_document):
    db = SessionLocal()
    try:
        doc = seed_document(db)
        future_job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract", document_id=doc.id)
        future_job.run_after = datetime.now(UTC) + timedelta(hours=1)
        db.commit()
        future_job_id = future_job.id
    finally:
        db.close()

    worker_id = f"test-worker-{uuid.uuid4().hex[:6]}"
    for _ in range(200):
        job = claim_next_job(worker_id)
        if job is None:
            break
        assert job.id != future_job_id, "job voi run_after trong tuong lai khong duoc claim"


def test_handle_job_error_transient_requeues_with_backoff(seed_document):
    db = SessionLocal()
    try:
        doc = seed_document(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract", document_id=doc.id, max_attempts=5)
        db.commit()
        job_id = job.id
    finally:
        db.close()

    handle_job_error(job_id, RuntimeError("transient network blip"))

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.queued
        assert job.attempts == 1
        assert job.run_after > datetime.now(UTC)
        assert job.locked_at is None
    finally:
        db.close()


def test_handle_job_error_terminal_fails_immediately_even_with_attempts_left(seed_document):
    db = SessionLocal()
    try:
        doc = seed_document(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract", document_id=doc.id, max_attempts=5)
        db.commit()
        job_id = job.id
        doc_id = doc.id
    finally:
        db.close()

    handle_job_error(job_id, ValueError("unsupported format"))

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.failed
        assert job.attempts == 1
        doc_after = db.get(Document, doc_id)
        assert doc_after.status == DocumentStatus.failed
    finally:
        db.close()


def test_handle_job_error_fails_after_max_attempts_even_if_transient(seed_document):
    db = SessionLocal()
    try:
        doc = seed_document(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract", document_id=doc.id, max_attempts=1)
        db.commit()
        job_id = job.id
    finally:
        db.close()

    handle_job_error(job_id, RuntimeError("still transient"))

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.failed
    finally:
        db.close()


def test_sweep_stuck_jobs_requeues_jobs_stuck_past_threshold(seed_document):
    db = SessionLocal()
    try:
        doc = seed_document(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="chunk", document_id=doc.id, max_attempts=5)
        job.status = JobStatus.running
        job.locked_at = datetime.now(UTC) - timedelta(minutes=20)
        job.locked_by = "dead-worker"
        db.commit()
        job_id = job.id
    finally:
        db.close()

    sweep_stuck_jobs()

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.queued
        assert job.attempts == 1
        assert job.locked_at is None
    finally:
        db.close()


def test_sweep_stuck_jobs_ignores_recently_locked_jobs(seed_document):
    db = SessionLocal()
    try:
        doc = seed_document(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="chunk", document_id=doc.id, max_attempts=5)
        job.status = JobStatus.running
        job.locked_at = datetime.now(UTC)
        job.locked_by = "alive-worker"
        db.commit()
        job_id = job.id
    finally:
        db.close()

    sweep_stuck_jobs()

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.running
        assert job.attempts == 0
    finally:
        db.close()


def test_sweep_stuck_jobs_fails_a_job_with_no_attempts_left_and_marks_its_document_failed(seed_document):
    """Một job kẹt mãi thì tài liệu của nó không bao giờ xong: đánh dấu thất bại để giao diện
    thôi hiện "đang xử lý" và giảng viên biết phải tải lại."""
    db = SessionLocal()
    try:
        doc = seed_document(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="embed", document_id=doc.id, max_attempts=2)
        job.status = JobStatus.running
        job.attempts = 1
        job.locked_at = datetime.now(UTC) - timedelta(minutes=20)
        job.locked_by = "dead-worker"
        db.commit()
        job_id, doc_id = job.id, doc.id
    finally:
        db.close()

    sweep_stuck_jobs()

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.failed
        assert job.locked_at is None
        assert db.get(Document, doc_id).status == DocumentStatus.failed
    finally:
        db.close()
