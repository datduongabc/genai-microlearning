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

from app.config import settings
from app.db import SessionLocal
from app.jobs.queue import (
    STUCK_THRESHOLD_SECONDS,
    claim_next_job,
    enqueue_job,
    execute_job_step,
    handle_job_error,
    heartbeat,
    is_terminal_error,
    run_claimed_job,
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
def test_unreadable_pdf_fails_on_first_extract_attempt(seed_document, tmp_path, monkeypatch, encrypted):
    import pymupdf

    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))

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
                          document_id=doc_id, payload={"storage_key": path.name})
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
    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))
    (tmp_path / "lecture.mp3").write_bytes(b"audio")
    with SessionLocal() as db:
        doc = seed_document(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract", document_id=doc.id,
                          payload={"storage_key": "lecture.mp3"})
        job.status, job.locked_by = JobStatus.running, "test-worker"  # a step runs only on a claimed job
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
        # The queue is claimed oldest first and the shared test DB holds jobs other tests left
        # queued. Dating this job to the past puts it ahead of them, so the test does not depend
        # on how many there are.
        my_job.created_at = datetime(2000, 1, 1, tzinfo=UTC)
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


def test_a_failure_is_written_with_its_target_or_not_at_all(seed_document, monkeypatch):
    """The job and what it was making fail in one commit: if marking the document fails, the job is
    not left failed beside a document that still says processing, which nothing would ever clear."""
    from app.jobs import queue

    db = SessionLocal()
    try:
        doc = seed_document(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract", document_id=doc.id, max_attempts=1)
        db.commit()
        job_id, doc_id = job.id, doc.id
    finally:
        db.close()

    def cut(db, job):
        raise ConnectionError("the database went away")
    monkeypatch.setattr(queue, "mark_target_failed", cut)
    with pytest.raises(ConnectionError):
        handle_job_error(job_id, ValueError("unsupported format"))

    db = SessionLocal()
    try:
        assert db.get(Job, job_id).status == JobStatus.queued
        assert db.get(Document, doc_id).status != DocumentStatus.failed
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
        assert job.system_failure  # the lecturer is not shown the sweeper's own note
        assert db.get(Document, doc_id).status == DocumentStatus.failed
    finally:
        db.close()


def _running_job_locked_long_ago(db, doc, worker_id: str) -> uuid.UUID:
    job = enqueue_job(db, job_type=JobType.module_generation, step="generate", document_id=doc.id, max_attempts=5)
    job.status = JobStatus.running
    job.locked_at = datetime.now(UTC) - timedelta(minutes=20)
    job.locked_by = worker_id
    db.commit()
    return job.id


def test_a_long_job_whose_worker_still_beats_is_not_requeued(seed_document):
    """Một lượt sinh module hay transcribe video có thể chạy quá ngưỡng kẹt (STUCK_THRESHOLD_SECONDS). Nếu sweeper
    coi nó là kẹt thì worker thứ hai chạy lại cùng job trong khi worker đầu vẫn chạy: hai module,
    gấp đôi lượt gọi model. Worker còn sống thì nhịp tim giữ job lại cho nó."""
    db = SessionLocal()
    try:
        job_id = _running_job_locked_long_ago(db, seed_document(db), "alive-worker")
    finally:
        db.close()

    assert heartbeat(job_id, "alive-worker") is True
    sweep_stuck_jobs()

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.running
        assert job.locked_by == "alive-worker"
        assert job.attempts == 0
    finally:
        db.close()


def test_a_worker_that_lost_its_job_cannot_beat_for_it(seed_document):
    """Job đã bị sweeper lấy lại và giao cho worker khác: nhịp tim của worker cũ không được giữ
    khóa thay cho worker mới, và báo cho worker cũ biết nó đã mất job."""
    db = SessionLocal()
    try:
        job_id = _running_job_locked_long_ago(db, seed_document(db), "new-worker")
        before = db.get(Job, job_id).locked_at
    finally:
        db.close()

    assert heartbeat(job_id, "old-worker") is False

    db = SessionLocal()
    try:
        assert db.get(Job, job_id).locked_at == before
    finally:
        db.close()


def test_running_a_claimed_job_keeps_beating_until_it_finishes(seed_document, monkeypatch):
    import time

    from app.jobs import queue

    db = SessionLocal()
    try:
        job = db.get(Job, _running_job_locked_long_ago(db, seed_document(db), "beating-worker"))
        db.expunge(job)
    finally:
        db.close()

    monkeypatch.setattr(queue, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(queue, "execute_job_step", lambda job: time.sleep(0.3))

    run_claimed_job(job, "beating-worker")

    db = SessionLocal()
    try:
        locked_at = db.get(Job, job.id).locked_at
        assert locked_at > datetime.now(UTC) - timedelta(minutes=1)
    finally:
        db.close()


def test_a_step_that_raises_is_still_handled_after_beating(seed_document, monkeypatch):
    from app.jobs import queue

    db = SessionLocal()
    try:
        job = db.get(Job, _running_job_locked_long_ago(db, seed_document(db), "raising-worker"))
        db.expunge(job)
    finally:
        db.close()

    def boom(job):
        raise RuntimeError("network hiccup")

    monkeypatch.setattr(queue, "execute_job_step", boom)
    run_claimed_job(job, "raising-worker")

    db = SessionLocal()
    try:
        current = db.get(Job, job.id)
        assert current.status == JobStatus.queued
        assert current.attempts == 1
        assert current.last_error == "network hiccup"
    finally:
        db.close()


def test_an_outage_waits_longer_than_an_ordinary_failure(seed_document, monkeypatch):
    """A 60 s gateway outage took a job to 3 of its 5 attempts at the usual backoff
    (docs/measurements/2026-11-06-failure-tests.md)."""
    import httpx

    from app.jobs import queue

    monkeypatch.setattr(queue.random, "random", lambda: 0.0)  # the shortest jitter
    delays = {}
    for name, err in {"outage": httpx.ConnectError("gateway down"), "other": RuntimeError("hiccup")}.items():
        db = SessionLocal()
        try:
            job = db.get(Job, _running_job_locked_long_ago(db, seed_document(db), f"{name}-worker"))
            db.expunge(job)
        finally:
            db.close()
        monkeypatch.setattr(queue, "execute_job_step", lambda job, err=err: (_ for _ in ()).throw(err))
        before = datetime.now(UTC)
        run_claimed_job(job, f"{name}-worker")
        with SessionLocal() as db:
            current = db.get(Job, job.id)
            assert current.status == JobStatus.queued and current.attempts == 1
            delays[name] = (current.run_after - before).total_seconds()

    assert delays["outage"] >= 0.75 * queue.OUTAGE_BASE_BACKOFF_SECONDS - 1
    assert delays["other"] < 0.75 * queue.OUTAGE_BASE_BACKOFF_SECONDS - 1


def test_sweep_skips_a_job_another_sweeper_is_processing(seed_document):
    # With several worker processes, sweepers overlap. Without the row lock the second one would
    # wait, then write its stale copy over the first one's result.
    with SessionLocal() as db:
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="extract", document_id=seed_document(db).id)
        job.status = JobStatus.running
        job.locked_at = datetime.now(UTC) - timedelta(seconds=STUCK_THRESHOLD_SECONDS + 60)
        job.locked_by = "dead-worker"
        db.commit()
        job_id = job.id
    holder = SessionLocal()
    try:
        holder.query(Job).filter(Job.id == job_id).with_for_update().one()
        sweep_stuck_jobs()
        holder.rollback()
    finally:
        holder.close()
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == JobStatus.running


def _hand_to(job_id: uuid.UUID, worker_id: str) -> None:
    """What the sweeper and a second claim do while the first worker is still running the job."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        job.locked_by = worker_id
        job.locked_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()


def test_a_worker_that_lost_its_job_does_not_write_its_failure(seed_document, monkeypatch):
    """The old worker's error must not put back in the queue, or fail, the job another worker now runs."""
    from app.jobs import queue

    db = SessionLocal()
    try:
        job = db.get(Job, _running_job_locked_long_ago(db, seed_document(db), "old-worker"))
        db.expunge(job)
    finally:
        db.close()

    def lose_then_fail(job):
        _hand_to(job.id, "new-worker")
        raise RuntimeError("late failure")

    monkeypatch.setattr(queue, "execute_job_step", lose_then_fail)
    run_claimed_job(job, "old-worker")

    db = SessionLocal()
    try:
        current = db.get(Job, job.id)
        assert (current.status, current.locked_by, current.attempts) == (JobStatus.running, "new-worker", 0)
        assert current.last_error is None
    finally:
        db.close()


def test_a_step_that_lost_its_job_does_not_write_its_result(seed_document):
    """The chunk step of a job the sweeper gave away keeps its result to itself: the new worker's
    run is the one that counts, and two runs must not both move the job on."""
    db = SessionLocal()
    try:
        doc = seed_document(db)
        job = enqueue_job(db, job_type=JobType.document_pipeline, step="chunk", document_id=doc.id,
                          payload={"units": [{"text": "Một đoạn văn đủ dài để thành một chunk.", "locator": {}}]})
        job.status, job.locked_by, job.locked_at = JobStatus.running, "old-worker", datetime.now(UTC)
        db.commit()
        db.refresh(job)
        db.expunge(job)
    finally:
        db.close()

    _hand_to(job.id, "new-worker")
    run_claimed_job(job, "old-worker")

    db = SessionLocal()
    try:
        current = db.get(Job, job.id)
        assert (current.step, current.status, current.locked_by) == ("chunk", JobStatus.running, "new-worker")
        assert "chunks" not in current.payload
    finally:
        db.close()


def test_a_render_is_taken_only_by_a_render_worker_and_the_rest_only_by_a_general_one(seed_document, monkeypatch):
    """An encode holds the task's CPU for minutes: on AWS it runs on tasks of its own, so that it does
    not slow every other job of a general worker, and two videos render on two tasks."""
    from app.config import settings

    db = SessionLocal()
    try:
        doc = seed_document(db)
        jobs = {kind: enqueue_job(db, job_type=kind, step="x", document_id=doc.id)
                for kind in (JobType.video_render, JobType.document_pipeline)}
        for job in jobs.values():
            # Ahead of whatever other tests left queued, as above.
            job.created_at = datetime(2000, 1, 1, tzinfo=UTC)
        db.commit()
        ids = {kind: job.id for kind, job in jobs.items()}
    finally:
        db.close()

    def first_taken(queue):
        monkeypatch.setattr(settings, "worker_queue", queue)
        taken = claim_next_job(f"test-{queue}")
        return taken.job_type if taken is not None and taken.id in ids.values() else None

    assert first_taken("general") == JobType.document_pipeline
    assert first_taken("render") == JobType.video_render
