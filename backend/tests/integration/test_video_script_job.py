import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db import SessionLocal
from app.generation import claim_evaluation, media_common, video_script
from app.jobs.failures import SYSTEM_FAILURE_MESSAGE
from app.jobs.queue import enqueue_job, execute_job_step, handle_job_error, sweep_stuck_jobs
from app.llm import catalog
from app.llm.gateway import requested_model
from app.models import Chunk, Course, Document, DocumentStatus, Job, JobStatus, JobType, MediaStatus, User, Video


@pytest.fixture(autouse=True)
def served(monkeypatch):
    """The aliases the gateway serves, without a running gateway."""
    monkeypatch.setattr(catalog, "is_available", lambda alias: alias in {"gemini-flash", "claude-fast"})


@pytest.fixture
def world():
    with SessionLocal() as db:
        user = User(email=f"vs-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Videos", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="x.pdf", status=DocumentStatus.embedded, raw_text="FCFS.")
        db.add(doc)
        db.flush()
        chunk = Chunk(document_id=doc.id, course_id=course.id, chunk_index=0, content="FCFS runs jobs in order.")
        db.add(chunk)
        video = Video(course_id=course.id, document_id=doc.id, focus="FCFS", output_language="vi",
                      model_alias="gemini-flash", voice_alias="heygen", image_alias="nano-banana")
        db.add(video)
        db.flush()
        job = enqueue_job(db, job_type=JobType.video_script, step="script", document_id=doc.id,
                          payload={"video_id": str(video.id)})
        job.status, job.locked_by = JobStatus.running, "w1"
        db.commit()
        ids = (user.id, course.id, video.id, job.id, chunk.id)
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids[1]).delete()
        db.query(User).filter(User.id == ids[0]).delete()
        db.commit()


def _scene(i):
    cited = [{"text": f"point {i}", "sources": ["S1"]}, {"text": f"point {i}b", "sources": ["S1"]}]
    return {"kind": "summary", "title": f"scene {i}", "bullets": cited,
            # Long enough that 8 scenes reach the 4-minute floor, so no top-up is asked for.
            "narration": [{"text": "x" * 520 + ".", "sources": ["S1"]}]}


def test_the_job_writes_the_script_and_finishes(world, monkeypatch):
    _, _, video_id, job_id, chunk_id = world
    with SessionLocal() as db:
        chunk = db.get(Chunk, chunk_id)
        db.expunge(chunk)
    monkeypatch.setattr(media_common, "retrieve", lambda *a, **k: [chunk])
    monkeypatch.setattr(video_script, "complete_structured", lambda prompt, model, **kw: model(
        scenes=[_scene(i) for i in range(video_script.MIN_SCENES)]))
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda prompt, model, **k: model(verdicts=[
        {"number": n, "supported": True, "reason": ""} for n in range(1, 4)]))
    with SessionLocal() as db:
        execute_job_step(db.get(Job, job_id))
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        assert video.status == MediaStatus.draft and video.draft_version == 1
        assert len(video.scenes) == video_script.MIN_SCENES
        assert video.scenes[0]["citations"] == [str(chunk_id)]
        assert video.cost["script"]["usd"] is None  # the faked model call is never priced
        assert video.cost["script"]["seconds"] >= 0
        assert db.get(Job, job_id).status == JobStatus.done


def test_a_video_made_with_an_alias_the_gateway_dropped_is_scripted_with_the_default(world, monkeypatch):
    from app.config import settings

    _, _, video_id, job_id, chunk_id = world
    with SessionLocal() as db:
        db.get(Video, video_id).model_alias = "groq-pro"
        db.commit()
        chunk = db.get(Chunk, chunk_id)
        db.expunge(chunk)
    writers = []
    monkeypatch.setattr(media_common, "retrieve", lambda *a, **k: [chunk])
    monkeypatch.setattr(video_script, "complete_structured", lambda prompt, model, **kw: writers.append(
        requested_model()) or model(scenes=[_scene(i) for i in range(video_script.MIN_SCENES)]))
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda prompt, model, **k: model(verdicts=[
        {"number": n, "supported": True, "reason": ""} for n in range(1, 4)]))
    with SessionLocal() as db:
        execute_job_step(db.get(Job, job_id))
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        assert video.status == MediaStatus.draft
        assert writers == [settings.llm_model] and video.model_alias == settings.llm_model


def test_a_job_that_runs_out_of_attempts_marks_the_video_failed(world):
    _, _, video_id, job_id, _ = world
    handle_job_error(job_id, ValueError("document is empty"), worker_id="w1")
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == JobStatus.failed
        video = db.get(Video, video_id)
        assert video.status == MediaStatus.failed and video.error == "document is empty"


def test_a_system_failure_shows_the_safe_message_not_the_exception(world):
    _, _, video_id, job_id, _ = world
    with SessionLocal() as db:
        db.get(Job, job_id).max_attempts = 1
        db.commit()
    handle_job_error(job_id, RuntimeError("litellm.InternalServerError: upstream 500 at 10.0.0.7"), worker_id="w1")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        assert video.status == MediaStatus.failed and video.error == SYSTEM_FAILURE_MESSAGE


def test_a_swept_job_past_its_attempts_marks_the_video_failed(world):
    _, _, video_id, job_id, _ = world
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.locked_at = datetime.now(UTC) - timedelta(hours=1)
        job.attempts, job.max_attempts = 4, 5
        db.commit()
    sweep_stuck_jobs()
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == JobStatus.failed
        video = db.get(Video, video_id)
        assert video.status == MediaStatus.failed and video.error == SYSTEM_FAILURE_MESSAGE


@pytest.mark.parametrize("payload", [{}, {"video_id": "not-a-uuid"}])
def test_a_failed_job_without_a_usable_video_id_still_fails_cleanly(world, payload):
    _, _, video_id, job_id, _ = world
    with SessionLocal() as db:
        db.get(Job, job_id).payload = payload
        db.commit()
    handle_job_error(job_id, ValueError("x"), worker_id="w1")
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == JobStatus.failed
        assert db.get(Video, video_id).status == MediaStatus.generating
