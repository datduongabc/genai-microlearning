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

import httpx
from litellm.exceptions import RateLimitError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db import WorkerSessionLocal
from app.jobs.failures import lecturer_message
from app.jobs.maintenance import purge_old_records
from app.llm.gateway import waiting_on_rate_limits
from app.lti.roster import sync_rosters
from app.models import Job, JobStatus, JobType

logger = logging.getLogger(__name__)

BASE_BACKOFF_SECONDS = 5.0
# When one of our own services is down (gateway, embedder, reranker, database), not the request. Five attempts at
# 30, 60, 120 and 240 s ride out about seven minutes of outage; at 5 s a two-minute outage failed the job for good
# (docs/measurements/2026-11-06-failure-tests.md).
OUTAGE_BASE_BACKOFF_SECONDS = 30.0
MAX_BACKOFF_SECONDS = 300.0
# A running job's worker refreshes locked_at every HEARTBEAT_INTERVAL_SECONDS, so only a job whose worker has died
# goes STUCK_THRESHOLD_SECONDS without it: five missed beats. A module generation or a long video can legitimately
# run longer than the threshold, and requeuing it then would run the same job twice at once. It was 15 minutes,
# which is how long a job held by a crashed worker waited (measured: 14 minutes).
STUCK_THRESHOLD_SECONDS = 5 * 60
HEARTBEAT_INTERVAL_SECONDS = 60
# After a failed beat, the next one comes this soon, so a worker whose database connection was cut refreshes its
# locks as soon as the database is back, before a sweeper that also waited for it can take them.
HEARTBEAT_RETRY_SECONDS = 5
SWEEP_INTERVAL_SECONDS = 60
# Periodic clean-up (finished jobs, dead refresh tokens): first run shortly
# after start so a backend that is restarted often still gets it, then hourly.
MAINTENANCE_INTERVAL_SECONDS = 60 * 60
MAINTENANCE_FIRST_RUN_DELAY_SECONDS = 60
# How often the sweeping worker looks for courses whose member list is due to be read from the LMS
# (app/lti/roster.py); how old a list may get is LTI_ROSTER_SYNC_MINUTES.
ROSTER_CHECK_INTERVAL_SECONDS = 5 * 60
POLL_INTERVAL_SECONDS = 2.0
# One CloudWatch data point a minute, the finest that standard resolution keeps.
METRICS_INTERVAL_SECONDS = 60


def jittered_backoff(attempts: int, base: float = BASE_BACKOFF_SECONDS) -> float:
    exp = min(base * (2 ** (attempts - 1)), MAX_BACKOFF_SECONDS)
    return exp * (0.75 + random.random() * 0.5)


_OUTAGE_STATUSES = {502, 503, 504}


def is_outage(err: BaseException) -> bool:
    """A service we depend on did not answer, or answered that it is unavailable: waiting longer is what helps.
    Looks through the causes, because litellm and SQLAlchemy wrap the connection error they got."""
    from litellm.exceptions import APIConnectionError, InternalServerError, ServiceUnavailableError
    from sqlalchemy.exc import InterfaceError, OperationalError

    outage = (httpx.TransportError, OperationalError, InterfaceError, APIConnectionError, InternalServerError,
              ServiceUnavailableError)
    seen: set[int] = set()
    current: BaseException | None = err
    while current is not None and id(current) not in seen:
        if isinstance(current, outage):
            return True
        if isinstance(current, httpx.HTTPStatusError) and current.response.status_code in _OUTAGE_STATUSES:
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


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
    study_id: uuid.UUID | None = None,
    payload: dict | None = None, max_attempts: int = 5,
) -> Job:
    job = Job(
        job_type=job_type, step=step, study_id=study_id, document_id=document_id, module_id=module_id,
        payload=payload or {}, max_attempts=max_attempts,
    )
    db.add(job)
    db.flush()
    return job


EDITOR_RETRY_MESSAGE = "Hãy bấm Vẽ lại / Dựng lại trong trình sửa video."


def retry_failed_job(db: Session, job_id: uuid.UUID) -> bool:
    """Puts a failed job back in the queue, to run again from the step it stopped at: attempts
    start over, it is due now, and nothing is uploaded or created again (the payload keeps the
    work done so far, and a module is only made when a generation finishes). False when there is
    nothing to retry for this caller: the job is not failed (someone retried it first), the
    lecturer already generated again, or it is neither the caller's nor the caller an admin.

    The one function behind both retry buttons, the lecturer's on their own jobs and the admin's
    on any: the work is the database function retry_failed_job (migration 0007), which an admin
    could not do with plain statements because row level security hides other lecturers' jobs from
    them. The caller commits."""
    return bool(db.scalar(select(func.retry_failed_job(job_id))))


class LostJob(Exception):
    """This worker no longer holds the job: the sweeper gave it to another worker while this one
    was still running it (no heartbeat for STUCK_THRESHOLD_SECONDS). The other run is the one that
    counts, so this one ends without writing anything."""


def hold(db: Session, job: Job) -> Job:
    """The job's row, locked until `db` commits, when the worker that claimed `job` still holds it.

    A step calls this right before it writes its result. The lock makes the check and the write one
    step: the sweeper locks with SKIP LOCKED, so it cannot hand the job away in between. Without
    the check, a worker that lost its job would still move it on, and both runs would write."""
    current = db.get(Job, job.id, with_for_update=True, populate_existing=True)
    if current is None or current.status != JobStatus.running or current.locked_by != job.locked_by:
        raise LostJob(f"job {job.id} is no longer held by {job.locked_by}")
    return current


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


def heartbeat(job_id: uuid.UUID, worker_id: str) -> bool:
    """Refresh the lock of a job this worker is still running. False when the job is no longer
    running under this worker (the sweeper gave it to another), which then must not be kept alive."""
    db = WorkerSessionLocal()
    try:
        result = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.running, Job.locked_by == worker_id)
            .values(locked_at=datetime.now(UTC))
        )
        db.commit()
        return result.rowcount == 1
    finally:
        db.close()


def _beat_until(stop: threading.Event, job_id: uuid.UUID, worker_id: str) -> None:
    wait = HEARTBEAT_INTERVAL_SECONDS
    while not stop.wait(wait):
        try:
            if not heartbeat(job_id, worker_id):
                logger.warning(f"[job_queue] worker {worker_id} no longer holds job {job_id}")
                return
            wait = HEARTBEAT_INTERVAL_SECONDS
        except Exception:
            logger.exception(f"[job_queue] heartbeat failed for job {job_id}")
            wait = HEARTBEAT_RETRY_SECONDS


_active_jobs = 0
_active_jobs_lock = threading.Lock()


def has_active_jobs() -> bool:
    """Whether this process is running a job now; see app/jobs/ecs_protection.py."""
    return _active_jobs > 0


def run_claimed_job(job: Job, worker_id: str) -> None:
    """Run a job this worker claimed, beating for it the whole time, and record a failure."""
    global _active_jobs
    with _active_jobs_lock:
        _active_jobs += 1
    try:
        _run_with_heartbeat(job, worker_id)
    finally:
        with _active_jobs_lock:
            _active_jobs -= 1


def _run_with_heartbeat(job: Job, worker_id: str) -> None:
    stop = threading.Event()
    beater = threading.Thread(target=_beat_until, args=(stop, job.id, worker_id), daemon=True)
    beater.start()
    try:
        with waiting_on_rate_limits():
            execute_job_step(job)
    except Exception as err:
        failure = err
    else:
        failure = None
    finally:
        stop.set()
        beater.join()
    if isinstance(failure, LostJob):
        logger.warning(f"[job_queue] worker {worker_id} lost job {job.id} while running it; its result is dropped")
    elif failure is not None:
        handle_job_error(job.id, failure, worker_id=worker_id)


def _payload_id(job: Job, key: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str((job.payload or {}).get(key)))
    except ValueError:
        logger.error(f"[job_queue] job {job.id} has no usable {key}")
        return None


def mark_target_failed(db: Session, job: Job) -> None:
    """What a job was making is failed along with it, so the lecturer sees an error and not a spinner
    that never ends. The one place both a job that fails (handle_job_error) and one the sweeper gives
    up on come through; a new job type that makes something adds its branch here. The caller commits."""
    from app.models import Document, DocumentStatus, FlashcardSet, MediaStatus, Video
    if job.job_type == JobType.document_pipeline and job.document_id is not None:
        doc = db.get(Document, job.document_id)
        if doc is not None:
            doc.status = DocumentStatus.failed
    elif job.job_type == JobType.flashcard_generation:
        set_id = _payload_id(job, "flashcard_set_id")
        card_set = db.get(FlashcardSet, set_id) if set_id is not None else None
        if card_set is not None:
            card_set.status = MediaStatus.failed
    elif job.job_type == JobType.video_script:
        video_id = _payload_id(job, "video_id")
        video = db.get(Video, video_id) if video_id is not None else None
        if video is not None:
            video.status = MediaStatus.failed
            # The video page shows why, as the job's error is not on it; a system failure shows the
            # safe message, as the document list does, since an exception's text is no help there.
            video.error = lecturer_message(job)
    elif job.job_type in (JobType.video_images, JobType.video_render):
        video_id = _payload_id(job, "video_id")
        video = db.get(Video, video_id) if video_id is not None else None
        if video is not None:
            video.status = MediaStatus.failed
            # A run clears the error when it starts (app/video/illustrations.py, render.py), so one set
            # now is this run's own, naming each scene it could not draw or voice; lecturer_message
            # would cut it at 200 characters. A system failure may have come before that start, so it
            # shows its own. A failed render leaves video_key alone: learners keep the video they had.
            if job.system_failure or not video.error:
                video.error = lecturer_message(job)


def handle_job_error(job_id: uuid.UUID, err: Exception, *, worker_id: str | None = None) -> None:
    """Records a failed attempt. With `worker_id`, only while that worker still holds the job: the
    error of a run the sweeper already gave away must not requeue or fail the run that replaced it."""
    db = WorkerSessionLocal()
    try:
        job = db.get(Job, job_id, with_for_update=worker_id is not None)
        if job is None:
            return
        if worker_id is not None and (job.status != JobStatus.running or job.locked_by != worker_id):
            logger.warning(f"[job_queue] worker {worker_id} lost job {job_id}; its error is dropped: {err}")
            return
        message = str(err)[:500]
        terminal = is_terminal_error(err)
        new_attempts = job.attempts + 1

        if not terminal and new_attempts < job.max_attempts:
            delay = jittered_backoff(new_attempts, OUTAGE_BASE_BACKOFF_SECONDS if is_outage(err) else BASE_BACKOFF_SECONDS)
            logger.warning(
                f"[job_queue] job {job_id} step={job.step} transient error, retrying in {delay:.1f}s: {message}"
            )
            job.status = JobStatus.queued
            job.attempts = new_attempts
            job.last_error = message
            job.run_after = datetime.now(UTC) + timedelta(seconds=delay)
            job.locked_at = None
            job.locked_by = None
            db.commit()
            return

        logger.error(f"[job_queue] job {job_id} step={job.step} FAILED ({new_attempts}/{job.max_attempts}): {message}")
        job.status = JobStatus.failed
        job.attempts = new_attempts
        job.last_error = message
        # Out of attempts on an error worth retrying: an outage, a rate limit or something unexpected,
        # not the input. A terminal error is a ValueError, whose message is meant for the lecturer.
        job.system_failure = not terminal
        job.locked_at = None
        job.locked_by = None
        # One commit with the job: a process stopped between two would leave the set or video
        # "generating" for good, which nothing clears and which cannot be deleted.
        mark_target_failed(db, job)
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
        # SKIP LOCKED: with several worker processes, sweepers overlap. A job another sweeper holds
        # is its to handle; waiting for it would then overwrite its result with this stale copy.
        stuck = (db.query(Job).filter(Job.status == JobStatus.running, Job.locked_at < cutoff)
                 .with_for_update(skip_locked=True).all())
        for job in stuck:
            new_attempts = job.attempts + 1
            logger.warning(f"[job_queue] sweeper: job {job.id} step={job.step} bi ket, locked_at={job.locked_at}")
            if new_attempts >= job.max_attempts:
                job.status = JobStatus.failed
                job.attempts = new_attempts
                job.last_error = "Job timeout (ket o trang thai running -- worker co the da restart giua chung)"
                job.system_failure = True
                job.locked_at = None
                job.locked_by = None
                mark_target_failed(db, job)
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
    elif job.job_type == JobType.flashcard_generation:
        from app.generation.flashcards import run_flashcard_step
        run_flashcard_step(job)
    elif job.job_type == JobType.video_script:
        from app.generation.video_script import run_video_script_step
        run_video_script_step(job)
    elif job.job_type == JobType.video_images:
        from app.video.illustrations import run_video_images_step
        run_video_images_step(job)
    elif job.job_type == JobType.video_render:
        from app.video.render import run_video_render_step
        run_video_render_step(job)
    elif job.job_type == JobType.study_judge:
        from app.study.judge import run_study_judge_step
        run_study_judge_step(job)
    else:
        raise ValueError(f"Unknown job_type: {job.job_type}")


_worker_threads: list[threading.Thread] = []
_worker_ids: list[str] = []
_stop_event = threading.Event()


def release_jobs_of(worker_ids: list[str]) -> int:
    """Puts the jobs these workers still hold back in the queue, due now and with the attempt not
    counted: the worker is stopping, the job did not fail. Without it they would sit as running
    until the sweeper's threshold, STUCK_THRESHOLD_SECONDS, on every deploy and every scale-in."""
    if not worker_ids:
        return 0
    db = WorkerSessionLocal()
    try:
        result = db.execute(
            update(Job)
            .where(Job.status == JobStatus.running, Job.locked_by.in_(worker_ids))
            .values(status=JobStatus.queued, locked_at=None, locked_by=None, run_after=func.now())
        )
        db.commit()
        return result.rowcount
    finally:
        db.close()


def _worker_loop(worker_id: str, *, run_sweeper: bool) -> None:
    logger.info(f"[job_queue] worker {worker_id} started (sweeper={run_sweeper})")
    next_sweep_at = datetime.now(UTC) + timedelta(seconds=SWEEP_INTERVAL_SECONDS)
    next_maintenance_at = datetime.now(UTC) + timedelta(seconds=MAINTENANCE_FIRST_RUN_DELAY_SECONDS)
    next_roster_check_at = next_maintenance_at

    while not _stop_event.is_set():
        # One thread per process sweeps. Several processes overlap safely, see sweep_stuck_jobs().
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
            if now >= next_roster_check_at:
                try:
                    sync_rosters()
                except Exception:
                    logger.exception("[job_queue] roster sync error")
                next_roster_check_at = datetime.now(UTC) + timedelta(seconds=ROSTER_CHECK_INTERVAL_SECONDS)

        job = None
        try:
            job = claim_next_job(worker_id)
            if job is not None:
                logger.info(f"[job_queue] executing job {job.id} type={job.job_type} step={job.step} attempt={job.attempts}")
                run_claimed_job(job, worker_id)
        except Exception:
            logger.exception("[job_queue] worker loop error")

        time.sleep(0.1 if job is not None else POLL_INTERVAL_SECONDS)

    logger.info(f"[job_queue] worker {worker_id} stopped")


def _metrics_loop() -> None:
    """A thread of its own: the sweeper's thread also runs jobs, and a backlog is exactly when it
    would be busy with a long one and the scaling signal would stop."""
    from app.jobs.metrics import report_queue_metrics

    while not _stop_event.wait(METRICS_INTERVAL_SECONDS):
        try:
            report_queue_metrics()
        except Exception:
            logger.exception("[job_queue] queue metrics error")


def start_worker() -> None:
    """Runs settings.worker_pool_size job threads in this process: the API process when
    run_job_worker is true, otherwise the worker process (app/jobs/worker.py). claim_next_job()
    uses FOR UPDATE SKIP LOCKED, so threads and processes never claim the same job."""
    global _worker_threads, _worker_ids
    if _worker_threads and any(t.is_alive() for t in _worker_threads):
        return
    _stop_event.clear()
    _worker_threads = []
    _worker_ids = []
    for i in range(settings.worker_pool_size):
        worker_id = f"worker-{uuid.uuid4().hex[:6]}"
        _worker_ids.append(worker_id)
        t = threading.Thread(target=_worker_loop, args=(worker_id,), kwargs={"run_sweeper": i == 0}, daemon=True)
        t.start()
        _worker_threads.append(t)
    if settings.queue_metrics:
        t = threading.Thread(target=_metrics_loop, daemon=True)
        t.start()
        _worker_threads.append(t)


def stop_worker() -> None:
    from app.ingestion import document_readers

    _stop_event.set()
    document_readers._ASR_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    # A job that ends within the grace finishes normally. ECS gives a stopping task stopTimeout
    # (120 s at most on Fargate) before it kills it, so the grace must stay below that.
    deadline = time.monotonic() + settings.worker_shutdown_grace_seconds
    for t in _worker_threads:
        t.join(timeout=max(0.0, deadline - time.monotonic()))
    if any(t.is_alive() for t in _worker_threads):
        # The process exits right after this, so a still-running job cannot write after its release.
        released = release_jobs_of(_worker_ids)
        if released:
            logger.warning(f"[job_queue] stopping with {released} job(s) unfinished, put back in the queue")
