"""Queue depth as CloudWatch metrics, which the worker service scales on.

Written as CloudWatch embedded metric format (EMF) lines on stdout: the awslogs driver ships them
with the other logs and CloudWatch turns them into metrics, so there is no PutMetricData call, no
SDK and no IAM permission for it. Each worker process reports the same counts, so the alarms and
the scaling policy read the Maximum, not the Sum.

Off unless QUEUE_METRICS=true: outside AWS nothing reads these lines.
"""

import json
import logging
import sys
import time

from sqlalchemy import func, select

from app.config import settings
from app.db import WorkerSessionLocal
from app.jobs.queue import RENDER_JOB_TYPES
from app.models import Job, JobStatus

logger = logging.getLogger(__name__)

NAMESPACE = "Microlearning/Jobs"


def queue_counts(db) -> dict[str, float]:
    """Jobs due now, jobs being run, and how long the oldest due job has waited.

    A job waiting out a retry backoff is queued but not due: it is not work a new worker could
    take, so counting it would scale up for nothing."""
    rendering = Job.job_type.in_(RENDER_JOB_TYPES)
    ready, render_ready, oldest = db.execute(
        select(func.count().filter(~rendering), func.count().filter(rendering),
               func.extract("epoch", func.now() - func.min(Job.run_after)))
        .where(Job.status == JobStatus.queued, Job.run_after <= func.now())
    ).one()
    running = db.scalar(select(func.count()).where(Job.status == JobStatus.running))
    # Two services, two signals: the general workers report both, since the render service is at
    # zero tasks when nothing waits for it and could not report that something does.
    return {
        "JobsReady": float(ready),
        "RenderJobsReady": float(render_ready),
        "JobsRunning": float(running),
        "OldestReadyJobAgeSeconds": float(oldest or 0.0),
    }


def emf_line(values: dict[str, float], *, timestamp_ms: int | None = None) -> str:
    units = {"OldestReadyJobAgeSeconds": "Seconds"}
    return json.dumps({
        "_aws": {
            "Timestamp": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [[]],
                "Metrics": [{"Name": name, "Unit": units.get(name, "Count")} for name in values],
            }],
        },
        **values,
    })


def report_queue_metrics() -> None:
    if not settings.queue_metrics:
        return
    db = WorkerSessionLocal()
    try:
        values = queue_counts(db)
    finally:
        db.close()
    # Straight to stdout, not through logging: EMF must be the whole line, and the JSON log
    # formatter would wrap it as a message.
    sys.stdout.write(emf_line(values) + "\n")
    sys.stdout.flush()
