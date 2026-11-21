"""The steps of a module generation already done, kept on its job (alembic 0051).

A module makes 14 to 25 model calls in sequence and is written only once all of them are done. A
run stopped part way, by scaling in, a deployment, a crash or a failed step, used to start over
and pay for every call again. Each finished step is now written to the job as JSON, committed at
once, and a later run of the same job reads it back and starts at the first step not done.
"""
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import Job


class Checkpoint:
    """Read and write one job's finished steps. A step is written as soon as it is done, in its own
    commit, so that it survives whatever stops the run next."""

    def __init__(self, db: Session, job_id: uuid.UUID):
        self._db = db
        self._job_id = job_id
        job = db.get(Job, job_id)
        self._steps: dict[str, Any] = dict(job.checkpoint or {}) if job is not None else {}

    def get(self, step: str) -> Any:
        return self._steps.get(step)

    def put(self, step: str, value: Any) -> None:
        self._steps[step] = value
        job = self._db.get(Job, self._job_id)
        if job is not None:
            # A new dict, so that the JSON column is seen as changed.
            job.checkpoint = dict(self._steps)
            self._db.commit()
