"""Durable job queue (Postgres FOR UPDATE SKIP LOCKED -- không cần Redis,
khớp triết lý "1 Postgres duy nhất" của kiến trúc). Chuyển
upload_document()/generate_module() từ 1 HTTP request đồng bộ, chặn (đã
thấy thật: 1 lần sinh module mất 10+ phút, không queue) sang xử lý nền,
poll trạng thái qua API.

Tham khảo thiết kế từ `jobs.py` (giữ ở `chunk_and_parse/`, code thật từ 1 dự
án khác của tác giả) -- claim FOR UPDATE SKIP LOCKED, retry/backoff phân
biệt lỗi tạm thời vs vĩnh viễn, sweep job "running" bị kẹt quá lâu (worker
crash giữa chừng). Khác bản gốc: chuyển sang sync SQLAlchemy (dự án này
không dùng async ORM) và KHÔNG resumable-per-batch -- bản gốc cần vậy vì
Speechmatics polling + quota embed_content rất chặt của dự án đó; ở đây mỗi
job step chạy đủ nhanh trong 1 lần nên retry nguyên step khi lỗi tạm thời là
đủ, không cần chia nhỏ hơn.
"""

import logging
import random
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from litellm.exceptions import RateLimitError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import WorkerSessionLocal
from app.jobs.maintenance import purge_old_records
from app.models import Job, JobStatus, JobType

logger = logging.getLogger(__name__)

BASE_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 300.0
STUCK_THRESHOLD_SECONDS = 15 * 60
SWEEP_INTERVAL_SECONDS = 60
# Periodic clean-up (finished jobs, dead refresh tokens): first run shortly
# after start so a backend that is restarted often still gets it, then hourly.
MAINTENANCE_INTERVAL_SECONDS = 60 * 60
MAINTENANCE_FIRST_RUN_DELAY_SECONDS = 60
POLL_INTERVAL_SECONDS = 2.0
# Self-audit (performance review) found jobs processed strictly one-at-a-time
# system-wide (1 thread) -- 2 giảng viên sinh module cùng lúc, người thứ 2
# chờ hết ~10 phút của người thứ 1 thay vì chỉ chờ lượt riêng. Không cần
# nginx/nhiều process: claim_next_job() đã dùng FOR UPDATE SKIP LOCKED nên N
# thread cùng gọi song song trong CHÍNH process backend là đủ an toàn -- vẫn
# khớp triết lý "1 process, không thêm hạ tầng" đã chọn từ đầu.
WORKER_POOL_SIZE = 4


def jittered_backoff(attempts: int) -> float:
    exp = min(BASE_BACKOFF_SECONDS * (2 ** (attempts - 1)), MAX_BACKOFF_SECONDS)
    return exp * (0.75 + random.random() * 0.5)


def is_terminal_error(err: Exception) -> bool:
    """Terminal = không đáng retry (lỗi cấu hình/dữ liệu, không phải flake
    mạng/quota tạm thời -- retry chỉ lặp lại đúng lỗi đó)."""
    if isinstance(err, RateLimitError):
        # Tường minh dù RateLimitError hiện không phải subclass của
        # ValueError/KeyError/TypeError -- đây là loại lỗi retry/backoff
        # nhắm tới, không được vô tình coi là terminal nếu litellm đổi
        # exception hierarchy sau này.
        return False
    return isinstance(err, (ValueError, KeyError, TypeError))


def enqueue_job(
    db: Session, *, job_type: JobType, step: str,
    document_id: uuid.UUID | None = None, module_id: uuid.UUID | None = None,
    section_id: uuid.UUID | None = None,
    payload: dict | None = None, max_attempts: int = 5,
) -> Job:
    job = Job(
        job_type=job_type, step=step, document_id=document_id, module_id=module_id,
        section_id=section_id, payload=payload or {}, max_attempts=max_attempts,
    )
    db.add(job)
    db.flush()
    return job


def claim_next_job(worker_id: str) -> Job | None:
    """Mở transaction riêng, tự commit -- FOR UPDATE SKIP LOCKED chỉ có tác
    dụng đúng nếu mỗi lần claim là 1 transaction độc lập (nhiều worker/nhiều
    lần gọi không thấy cùng 1 row đang bị khóa bởi lần gọi khác)."""
    db = WorkerSessionLocal()
    try:
        # So sánh với now() CỦA POSTGRES (func.now()), không phải đồng hồ
        # Python -- lệch giờ dù nhỏ giữa app/DB (rất thật trong container,
        # nhất là dưới tải) khiến 1 job vừa enqueue có run_after tính theo
        # server_default=func.now() của DB đứng SAU đồng hồ Python 1 chút,
        # tạm thời "vô hình" với điều kiện <=. Dùng cùng 1 đồng hồ (server)
        # cho cả 2 đầu loại hẳn lớp lỗi này, không chỉ vá 1 lần chạy cụ thể.
        job = (
            db.query(Job)
            .filter(Job.status == JobStatus.queued, Job.run_after <= func.now())
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .first()
        )
        if job is None:
            return None
        job.status = JobStatus.running
        job.locked_at = datetime.now(UTC)
        job.locked_by = worker_id
        db.commit()
        db.refresh(job)
        db.expunge(job)
        return job
    finally:
        db.close()


def handle_job_error(job_id: uuid.UUID, err: Exception) -> None:
    db = WorkerSessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        message = str(err)[:500]
        terminal = is_terminal_error(err)
        new_attempts = job.attempts + 1

        if not terminal and new_attempts < job.max_attempts:
            delay = jittered_backoff(new_attempts)
            logger.warning(
                f"[job_queue] job {job_id} step={job.step} loi tam thoi, retry sau {delay:.1f}s: {message}"
            )
            job.status = JobStatus.queued
            job.attempts = new_attempts
            job.last_error = message
            job.run_after = datetime.now(UTC) + timedelta(seconds=delay)
            job.locked_at = None
            job.locked_by = None
            db.commit()
            return

        logger.error(f"[job_queue] job {job_id} step={job.step} THAT BAI ({new_attempts}/{job.max_attempts}): {message}")
        job.status = JobStatus.failed
        job.attempts = new_attempts
        job.last_error = message
        job.locked_at = None
        job.locked_by = None
        db.commit()

        if job.job_type == JobType.document_pipeline and job.document_id is not None:
            from app.models import Document, DocumentStatus
            doc = db.get(Document, job.document_id)
            if doc is not None:
                doc.status = DocumentStatus.failed
                db.commit()
    finally:
        db.close()


def sweep_stuck_jobs() -> None:
    """Job 'running' kẹt quá STUCK_THRESHOLD_SECONDS (worker crash/reload
    giữa chừng, thấy thật trong session này khi --reload kích hoạt lúc job
    đang chạy) -- requeue nếu còn attempt, terminal-fail nếu hết."""
    db = WorkerSessionLocal()
    try:
        cutoff = datetime.now(UTC) - timedelta(seconds=STUCK_THRESHOLD_SECONDS)
        stuck = db.query(Job).filter(Job.status == JobStatus.running, Job.locked_at < cutoff).all()
        for job in stuck:
            new_attempts = job.attempts + 1
            logger.warning(f"[job_queue] sweeper: job {job.id} step={job.step} bi ket, locked_at={job.locked_at}")
            if new_attempts >= job.max_attempts:
                job.status = JobStatus.failed
                job.attempts = new_attempts
                job.last_error = "Job timeout (ket o trang thai running -- worker co the da restart giua chung)"
                job.locked_at = None
                job.locked_by = None
                if job.job_type == JobType.document_pipeline and job.document_id is not None:
                    from app.models import Document, DocumentStatus
                    doc = db.get(Document, job.document_id)
                    if doc is not None:
                        doc.status = DocumentStatus.failed
            else:
                job.status = JobStatus.queued
                job.attempts = new_attempts
                job.last_error = f"Requeued sau khi bi ket (attempt {new_attempts}/{job.max_attempts})"
                job.locked_at = None
                job.locked_by = None
                job.run_after = datetime.now(UTC)
        db.commit()
    finally:
        db.close()


def execute_job_step(job: Job) -> None:
    """Dispatch theo job_type -- import trong hàm để tránh vòng import
    (job_queue.py là service tầng thấp, executor thật nằm ở tầng pipeline)."""
    if job.job_type == JobType.document_pipeline:
        from app.ingestion.document_pipeline import run_document_pipeline_step
        run_document_pipeline_step(job)
    elif job.job_type == JobType.module_generation:
        from app.generation.module_generation_pipeline import run_module_generation_step
        run_module_generation_step(job)
    else:
        raise ValueError(f"Unknown job_type: {job.job_type}")


_worker_threads: list[threading.Thread] = []
_stop_event = threading.Event()


def _worker_loop(worker_id: str, *, run_sweeper: bool) -> None:
    logger.info(f"[job_queue] worker {worker_id} started (sweeper={run_sweeper})")
    next_sweep_at = datetime.now(UTC) + timedelta(seconds=SWEEP_INTERVAL_SECONDS)
    next_maintenance_at = datetime.now(UTC) + timedelta(seconds=MAINTENANCE_FIRST_RUN_DELAY_SECONDS)

    while not _stop_event.is_set():
        # Chỉ 1 trong N worker chạy sweeper (worker đầu tiên) -- sweep_stuck_jobs()
        # an toàn khi chạy trùng (chỉ requeue job thật sự kẹt), nhưng để N worker
        # cùng chạy trên cùng lịch là lãng phí không cần thiết.
        if run_sweeper:
            now = datetime.now(UTC)
            if now >= next_sweep_at:
                try:
                    sweep_stuck_jobs()
                except Exception:
                    logger.exception("[job_queue] sweeper error")
                next_sweep_at = datetime.now(UTC) + timedelta(seconds=SWEEP_INTERVAL_SECONDS)
            # Same designated worker as the sweeper: the clean-up is safe to
            # run twice but pointless to run on every thread.
            if now >= next_maintenance_at:
                try:
                    purge_old_records()
                except Exception:
                    logger.exception("[job_queue] maintenance error")
                next_maintenance_at = datetime.now(UTC) + timedelta(seconds=MAINTENANCE_INTERVAL_SECONDS)

        job = None
        try:
            job = claim_next_job(worker_id)
            if job is not None:
                logger.info(f"[job_queue] executing job {job.id} type={job.job_type} step={job.step} attempt={job.attempts}")
                try:
                    execute_job_step(job)
                except Exception as err:
                    handle_job_error(job.id, err)
        except Exception:
            logger.exception("[job_queue] worker loop error")

        time.sleep(0.1 if job is not None else POLL_INTERVAL_SECONDS)

    logger.info(f"[job_queue] worker {worker_id} stopped")


def start_worker() -> None:
    """Chạy WORKER_POOL_SIZE background thread trong CHÍNH process backend
    (không thêm container/service/reverse-proxy mới trong docker-compose --
    khớp quy mô đồ án solo, xem Architecture_GD2.md mục DevOps). FastAPI
    endpoint đồng bộ đã chạy trong thread pool riêng của uvicorn nên các
    thread nền này không chặn event loop xử lý request khác.

    N thread cùng gọi claim_next_job() song song AN TOÀN vì hàm đó dùng
    `FOR UPDATE SKIP LOCKED` (Postgres tự đảm bảo 2 thread không bao giờ
    claim trùng 1 job) -- không cần thêm lock/coordination riêng ở tầng
    Python. Đây là lý do tăng từ 1 lên N thread chỉ cần đổi đúng chỗ này,
    không cần đổi hạ tầng (không nginx, không nhiều process/container)."""
    global _worker_threads
    if _worker_threads and any(t.is_alive() for t in _worker_threads):
        return
    _stop_event.clear()
    _worker_threads = []
    for i in range(WORKER_POOL_SIZE):
        worker_id = f"worker-{uuid.uuid4().hex[:6]}"
        t = threading.Thread(target=_worker_loop, args=(worker_id,), kwargs={"run_sweeper": i == 0}, daemon=True)
        t.start()
        _worker_threads.append(t)


def stop_worker() -> None:
    _stop_event.set()
    for t in _worker_threads:
        t.join(timeout=5)
