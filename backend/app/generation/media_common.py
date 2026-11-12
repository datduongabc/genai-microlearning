"""What the generation of a flashcard set and of a video script share: the topic their passages are
retrieved for, a session that does not reopen a transaction while a model is answering, and the
job step that runs one of them."""

import contextlib
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.db import WorkerSessionLocal
from app.generation.module_generation_pipeline import _get_or_cache_planner
from app.jobs.queue import hold
from app.models import Document, Job, JobStatus


def focus_query(db: Session, doc: Document, item) -> str:
    """`item` is a FlashcardSet or a Video: both carry `focus` and `output_language`."""
    if item.focus and item.focus.strip():
        return item.focus.strip()
    # No focus: the topic the planner ranks first, as a module of this document would be about.
    concepts = _get_or_cache_planner(db, doc, item.output_language).concepts
    first = min(concepts, key=lambda c: c.priority)
    return f"{first.name}: {first.summary}"


@contextlib.contextmanager
def values_kept_across_commits(db: Session):
    """Read values stay in memory across the commits, so no prompt reopens a transaction by reading
    an expired attribute while a model is being called: that transaction would hold a database
    connection idle for as long as the model takes."""
    expire_on_commit = db.expire_on_commit
    db.expire_on_commit = False
    try:
        yield
    finally:
        db.expire_on_commit = expire_on_commit


def run_generation_step(
    job: Job, payload_key: str, generate: Callable[..., None],
) -> None:
    """Runs `generate(db, target_id, before_save=...)` for the item the job's payload names, in a
    session of its own. `before_save` marks the job done in the transaction that writes the result,
    so an item is never ready with its job still running, and a worker that lost the job writes
    nothing (app/jobs/queue.py, hold)."""
    target_id = uuid.UUID(job.payload[payload_key])
    db = WorkerSessionLocal()

    def finish() -> None:
        hold(db, job).status = JobStatus.done

    try:
        generate(db, target_id, before_save=finish)
    finally:
        db.close()
