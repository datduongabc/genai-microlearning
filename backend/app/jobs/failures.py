"""Failed jobs that still need the lecturer's attention.

A failed generation creates no module, and a failed document stays listed as "Lỗi", so the job
row is the only record of what went wrong. A failure is open until a newer job of the same kind
exists for the same document and the same set or video (the lecturer generated again, or a retry
is running): the same rule the database applies for the admin's view and for Retry
(open_failed_jobs, migration 0042).
"""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from app.models import Job, JobStatus, JobType

# What the lecturer reads on screen: the first part of the worker's message, which can run to
# 500 characters of a provider's exception text.
MAX_ERROR_CHARS = 200
# In place of that message when the system failed the job: an exception's text tells the lecturer
# nothing they can act on. It stays in last_error for the admin.
SYSTEM_FAILURE_MESSAGE = ("Hệ thống tạm thời không xử lý được (dịch vụ mô hình hoặc kết nối gặp sự cố). "
                          "Hãy bấm Thử lại sau ít phút. Nếu vẫn lỗi, hãy báo quản trị viên.")


# A failed picture or render is tried again from the video editor ("Vẽ lại", "Dựng lại"), never by
# a plain retry: those routes lock the video, wait for a job in flight, and count the day's limit.
EDITOR_RETRIED_JOBS = (JobType.video_images, JobType.video_render)


def lecturer_message(job: Job) -> str:
    """What the lecturer is shown for a failed job."""
    return SYSTEM_FAILURE_MESSAGE if job.system_failure else (job.last_error or "")[:MAX_ERROR_CHARS]


def status_error(job: Job) -> str | None:
    """The error a job status route shows. None until the job has failed: a job waiting for its next try
    holds the raw exception of the last one, and system_failure is only decided when the tries run out."""
    return lecturer_message(job) if job.status == JobStatus.failed else None


def _open_failure_filter():
    newer = aliased(Job)
    return (
        Job.status == JobStatus.failed,
        ~exists().where(
            newer.document_id == Job.document_id,
            newer.job_type == Job.job_type,
            # Per set and per video: one document has many, and set B made again does not fix set A.
            newer.payload["flashcard_set_id"].astext.is_not_distinct_from(Job.payload["flashcard_set_id"].astext),
            newer.payload["video_id"].astext.is_not_distinct_from(Job.payload["video_id"].astext),
            newer.created_at > Job.created_at,
        ),
    )


def open_failures(db: Session, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    """The open failures of each document, newest first. Documents without any are absent."""
    rows = db.scalars(
        select(Job)
        # A failed picture or render is shown and retried in the video editor, which reads the
        # video's own error; listed here, it would mark a healthy document as failed.
        .where(Job.document_id.in_(document_ids), Job.job_type.not_in(EDITOR_RETRIED_JOBS),
               *_open_failure_filter())
        .order_by(Job.created_at.desc())
    ).all()
    failures: dict[uuid.UUID, list[dict]] = {}
    for job in rows:
        failures.setdefault(job.document_id, []).append({
            "job_id": job.id,
            "job_type": job.job_type,
            "error": lecturer_message(job),
        })
    return failures
