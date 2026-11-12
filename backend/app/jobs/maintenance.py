"""Clean-up of rows that only ever accumulate: finished jobs and refresh tokens
that can no longer be used. Run periodically by the job worker (see
job_queue._worker_loop), not per request."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_

from app.config import settings
from app.db import WorkerSessionLocal
from app.models import (
    Document,
    DocumentStatus,
    Job,
    JobStatus,
    LtiLaunchCode,
    LtiLoginState,
    RefreshToken,
    StudyFile,
)
from app.storage import study_file_key, study_file_store, upload_key, upload_store

logger = logging.getLogger(__name__)

# A revoked refresh token is rejected either way (revoked_at is checked, and a
# deleted row is "not found"), so it is only kept briefly to help diagnose a
# rotation problem.
REVOKED_REFRESH_TOKEN_GRACE = timedelta(days=1)
# A presigned POST expires after 15 minutes (app/storage.py); an hour leaves a slow upload that
# started in time to finish and be completed.
ABANDONED_UPLOAD_AGE = timedelta(hours=1)


def purge_old_records() -> dict[str, int]:
    """Returns how many rows were removed, per kind.

    Queued and running jobs are never touched, however old: they are either
    still to run or the sweeper's business. Failed jobs outlive finished ones
    because the monitoring page is how failures get noticed."""
    now = datetime.now(UTC)
    db = WorkerSessionLocal()
    try:
        finished_jobs = (
            db.query(Job)
            .filter(Job.status == JobStatus.done, Job.created_at < now - timedelta(days=settings.done_job_retention_days))
            .delete(synchronize_session=False)
        )
        failed_jobs = (
            db.query(Job)
            .filter(
                Job.status == JobStatus.failed,
                Job.created_at < now - timedelta(days=settings.failed_job_retention_days),
            )
            .delete(synchronize_session=False)
        )
        tokens = (
            db.query(RefreshToken)
            .filter(
                or_(
                    RefreshToken.expires_at < now,
                    RefreshToken.revoked_at < now - REVOKED_REFRESH_TOKEN_GRACE,
                )
            )
            .delete(synchronize_session=False)
        )
        # LTI logins that were started and never finished, and launch codes never traded.
        lti = sum(
            db.query(model).filter(model.expires_at < now).delete(synchronize_session=False)
            for model in (LtiLoginState, LtiLaunchCode)
        )
        # Documents whose file never arrived: the client stopped between asking where to send it
        # and reporting it sent. The rows go first; a file left without its row is harmless and
        # S3's lifecycle expires it anyway.
        abandoned = (
            db.query(Document)
            .filter(Document.status == DocumentStatus.awaiting_upload,
                    Document.created_at < now - ABANDONED_UPLOAD_AGE)
            .all()
        )
        abandoned_keys = [upload_key(doc.id, doc.filename) for doc in abandoned]
        for doc in abandoned:
            db.delete(doc)
        # The same for a study's reference files. Their prefix has no lifecycle rule, so the file
        # goes too, if part of it arrived.
        abandoned_files = (
            db.query(StudyFile)
            .filter(StudyFile.status == "awaiting_upload", StudyFile.created_at < now - ABANDONED_UPLOAD_AGE)
            .all()
        )
        abandoned_file_keys = [study_file_key(file.id, file.filename) for file in abandoned_files]
        for file in abandoned_files:
            db.delete(file)
        db.commit()
    finally:
        db.close()
    store = upload_store()
    for key in abandoned_keys:
        store.delete(key)
    files = study_file_store()
    for key in abandoned_file_keys:
        files.delete(key)

    removed = {"jobs": finished_jobs + failed_jobs, "refresh_tokens": tokens, "lti": lti,
               "abandoned_uploads": len(abandoned_keys) + len(abandoned_file_keys)}
    if any(removed.values()):
        logger.info(
            f"[maintenance] removed {removed['jobs']} old job(s), {removed['refresh_tokens']} dead refresh token(s), "
            f"{removed['abandoned_uploads']} abandoned upload(s)"
        )
    return removed
