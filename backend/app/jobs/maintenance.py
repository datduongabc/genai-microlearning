"""Clean-up of rows that only ever accumulate: finished jobs and refresh tokens
that can no longer be used. Run periodically by the job worker (see
job_queue._worker_loop), not per request."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_

from app.config import settings
from app.db import WorkerSessionLocal
from app.models import Job, JobStatus, RefreshToken

logger = logging.getLogger(__name__)

# A revoked refresh token is rejected either way (revoked_at is checked, and a
# deleted row is "not found"), so it is only kept briefly to help diagnose a
# rotation problem.
REVOKED_REFRESH_TOKEN_GRACE = timedelta(days=1)


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
        db.commit()
    finally:
        db.close()

    removed = {"jobs": finished_jobs + failed_jobs, "refresh_tokens": tokens}
    if any(removed.values()):
        logger.info(
            f"[maintenance] removed {removed['jobs']} old job(s), {removed['refresh_tokens']} dead refresh token(s)"
        )
    return removed
