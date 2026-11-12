"""Failed jobs that still need the lecturer's attention.

A failed generation creates no module, and a failed document stays listed as "Lỗi", so the job
row is the only record of what went wrong. A failure is open until a newer job of the same kind
exists for the same document (the lecturer generated again, or a retry is running):
the same rule the database applies for the admin's view and for Retry (open_failed_jobs, migration 0007).
"""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from app.models import Job, JobStatus

# What the lecturer reads on screen: the first part of the worker's message, which can run to
# 500 characters of a provider's exception text.
MAX_ERROR_CHARS = 200
# In place of that message when the system failed the job: an exception's text tells the lecturer
# nothing they can act on. It stays in last_error for the admin.
SYSTEM_FAILURE_MESSAGE = ("Hệ thống tạm thời không xử lý được (dịch vụ mô hình hoặc kết nối gặp sự cố). "
                          "Hãy bấm Thử lại sau ít phút; nếu vẫn lỗi, báo quản trị viên.")


def lecturer_message(job: Job) -> str:
    """What the lecturer is shown for a failed job."""
    return SYSTEM_FAILURE_MESSAGE if job.system_failure else (job.last_error or "")[:MAX_ERROR_CHARS]


def _open_failure_filter():
    newer = aliased(Job)
    return (
        Job.status == JobStatus.failed,
        ~exists().where(
            newer.document_id == Job.document_id,
            newer.job_type == Job.job_type,
            newer.created_at > Job.created_at,
        ),
    )


def open_failures(db: Session, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    """The open failures of each document, newest first. Documents without any are absent."""
    rows = db.scalars(
        select(Job)
        .where(Job.document_id.in_(document_ids), *_open_failure_filter())
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
