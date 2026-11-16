import uuid

import pytest

from app.db import SessionLocal
from app.generation import claim_evaluation, flashcards, media_common
from app.generation.claim_evaluation import SupportVerdict
from app.jobs.queue import enqueue_job, execute_job_step, handle_job_error
from app.models import (
    Chunk,
    Course,
    Document,
    DocumentStatus,
    FlashcardSet,
    Job,
    JobStatus,
    JobType,
    MediaStatus,
    User,
)


@pytest.fixture
def world():
    with SessionLocal() as db:
        user = User(email=f"fc-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Flashcards", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="x.pdf", status=DocumentStatus.embedded, raw_text="Heaps.")
        db.add(doc)
        db.flush()
        chunk = Chunk(document_id=doc.id, course_id=course.id, chunk_index=0, content="A heap is a tree.")
        db.add(chunk)
        card_set = FlashcardSet(course_id=course.id, document_id=doc.id, focus="heaps", output_language="en",
                                model_alias="gemini-fast")
        db.add(card_set)
        db.flush()
        job = enqueue_job(db, job_type=JobType.flashcard_generation, step="generate", document_id=doc.id,
                          payload={"flashcard_set_id": str(card_set.id)})
        job.status, job.locked_by = JobStatus.running, "w1"
        db.commit()
        ids = (user.id, course.id, card_set.id, job.id, chunk.id)
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids[1]).delete()
        db.query(User).filter(User.id == ids[0]).delete()
        db.commit()


def test_the_job_fills_the_set_and_finishes(world, monkeypatch):
    _, _, set_id, job_id, chunk_id = world
    with SessionLocal() as db:
        chunk = db.get(Chunk, chunk_id)
        db.expunge(chunk)
    monkeypatch.setattr(media_common, "retrieve", lambda *a, **k: [chunk])
    monkeypatch.setattr(flashcards, "complete_structured", lambda prompt, model, **kw: model(
        cards=[{"front": "What is a heap?", "back": "A tree.", "sources": ["S1"]}]))
    monkeypatch.setattr(claim_evaluation, "complete_structured",
                        lambda *a, **k: SupportVerdict(supported=True, reason=""))
    with SessionLocal() as db:
        execute_job_step(db.get(Job, job_id))
    with SessionLocal() as db:
        card_set = db.get(FlashcardSet, set_id)
        assert card_set.status == MediaStatus.draft
        assert card_set.cards[0]["citations"] == [str(chunk_id)]
        assert card_set.cost["calls"] == 0 and card_set.cost["usd"] is None
        assert db.get(Job, job_id).status == JobStatus.done


def test_a_job_that_runs_out_of_attempts_marks_the_set_failed(world):
    _, _, set_id, job_id, _ = world
    handle_job_error(job_id, ValueError("document is empty"), worker_id="w1")
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == JobStatus.failed
        assert db.get(FlashcardSet, set_id).status == MediaStatus.failed


def test_a_swept_job_past_its_attempts_marks_the_set_failed(world):
    from datetime import UTC, datetime, timedelta

    from app.jobs.queue import sweep_stuck_jobs
    _, _, set_id, job_id, _ = world
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.locked_at = datetime.now(UTC) - timedelta(hours=1)
        job.attempts, job.max_attempts = 4, 5
        db.commit()
    sweep_stuck_jobs()
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == JobStatus.failed
        assert db.get(FlashcardSet, set_id).status == MediaStatus.failed


@pytest.mark.parametrize("payload", [{}, {"flashcard_set_id": "not-a-uuid"}])
def test_a_failed_job_without_a_usable_set_id_still_fails_cleanly(world, payload):
    _, _, _, job_id, _ = world
    with SessionLocal() as db:
        db.get(Job, job_id).payload = payload
        db.commit()
    handle_job_error(job_id, ValueError("x"), worker_id="w1")
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == JobStatus.failed
