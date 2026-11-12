"""The video_images job through the queue, on the real database and the local media store."""

import io
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image

from app.db import SessionLocal
from app.generation.video_script import Scene
from app.jobs.failures import SYSTEM_FAILURE_MESSAGE
from app.jobs.queue import enqueue_job, execute_job_step, handle_job_error, run_claimed_job
from app.llm.media import GeneratedImage, MediaError
from app.models import Course, Document, DocumentStatus, Job, JobStatus, JobType, MediaStatus, User, Video
from app.video import illustrations


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (400, 300), (23, 182, 214)).save(out, "PNG")
    return out.getvalue()


def _scene(title, kind="illustration", prompt="A queue with P1 and P2"):
    return Scene(kind=kind, title=title, bullets=["a", "b"], narration="Một câu.", citations=[],
                 image_prompt=prompt if kind == "illustration" else None).model_dump()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_storage_dir", str(tmp_path))
    with SessionLocal() as db:
        user = User(email=f"vi-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Videos", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="x.pdf", status=DocumentStatus.embedded, raw_text="FCFS.")
        db.add(doc)
        db.flush()
        video = Video(course_id=course.id, document_id=doc.id, focus="FCFS", output_language="vi",
                      model_alias="gemini-fast", voice_alias="voice-heygen", image_alias="image-nano-banana",
                      status=MediaStatus.draft,
                      scenes=[_scene("Hàng đợi"), _scene("Mở đầu", kind="title"), _scene("Gantt", prompt="A Gantt")])
        db.add(video)
        db.flush()
        job = enqueue_job(db, job_type=JobType.video_images, step="images", document_id=doc.id,
                          payload={"video_id": str(video.id)})
        job.status, job.locked_by = JobStatus.running, "w1"
        db.commit()
        ids = SimpleNamespace(user=user.id, course=course.id, video=video.id, job=job.id)
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids.course).delete()
        db.query(User).filter(User.id == ids.user).delete()
        db.commit()


def _files(tmp_path) -> list:
    return [p for p in (tmp_path / "media").rglob("*") if p.is_file()] if (tmp_path / "media").exists() else []


def test_the_job_draws_the_illustrations_and_finishes(world, monkeypatch, tmp_path):
    statuses = []

    def fake(alias, prompt, aspect_ratio="4:3", **kw):
        with SessionLocal() as db:
            statuses.append(db.get(Video, world.video).status)
        return GeneratedImage(_png(), "image/png", "google/gemini-nano-banana-2.1", 0.04)

    monkeypatch.setattr(illustrations, "generate_image", fake)
    with SessionLocal() as db:
        execute_job_step(db.get(Job, world.job))
    with SessionLocal() as db:
        video = db.get(Video, world.video)
        assert statuses == [MediaStatus.drawing] * 2
        assert video.status == MediaStatus.draft and video.error is None
        assert [bool(s["image_key"]) for s in video.scenes] == [True, False, True]
        assert all(s["image_key"].startswith(f"videos/{world.video}/img-") for s in (video.scenes[0], video.scenes[2]))
        assert video.cost["images"] == {"usd": pytest.approx(0.08), "images": 2}
        assert db.get(Job, world.job).status == JobStatus.done
    assert len(_files(tmp_path)) == 2


def test_the_listed_scenes_are_the_ones_drawn(world, monkeypatch):
    with SessionLocal() as db:
        db.get(Job, world.job).payload = {"video_id": str(world.video), "scene_indexes": [2]}
        db.commit()
    prompts = []
    monkeypatch.setattr(illustrations, "generate_image", lambda alias, prompt, **kw: prompts.append(prompt) or
                        GeneratedImage(_png(), "image/png", "x", None))
    with SessionLocal() as db:
        execute_job_step(db.get(Job, world.job))
    assert len(prompts) == 1 and "A Gantt" in prompts[0]


def test_a_scene_without_an_image_fails_the_job_once_and_names_it(world, monkeypatch, tmp_path):
    """Review Focus 2 through the worker: the job fails for good, not by retrying, the video
    shows the full message, and the scene that was drawn keeps its picture."""
    def fake(alias, prompt, **kw):
        if "Gantt" in prompt:
            raise MediaError("returned no image")
        return GeneratedImage(_png(), "image/png", "x", 0.04)

    monkeypatch.setattr(illustrations, "generate_image", fake)
    with SessionLocal() as db:
        job = db.get(Job, world.job)
        db.expunge(job)
    run_claimed_job(job, "w1")
    with SessionLocal() as db:
        job = db.get(Job, world.job)
        video = db.get(Video, world.video)
        assert job.status == JobStatus.failed and job.attempts == 1
        assert video.status == MediaStatus.failed
        assert "cảnh 3 (Gantt)" in video.error and "Hàng đợi" not in video.error
        assert video.scenes[0]["image_key"] and video.scenes[2]["image_key"] is None
        assert video.scenes[2]["image_prompt"] == "A Gantt"
    assert len(_files(tmp_path)) == 1


def test_a_video_deleted_while_drawing_leaves_no_files(world, monkeypatch, tmp_path):
    def fake(alias, prompt, **kw):
        with SessionLocal() as db:
            db.query(Video).filter(Video.id == world.video).delete()
            db.commit()
        return GeneratedImage(_png(), "image/png", "x", None)

    monkeypatch.setattr(illustrations, "generate_image", fake)
    with SessionLocal() as db:
        execute_job_step(db.get(Job, world.job))
    assert _files(tmp_path) == []
    with SessionLocal() as db:
        assert db.get(Job, world.job).status == JobStatus.done


def test_a_failed_job_marks_the_video_failed(world):
    handle_job_error(world.job, ValueError("Cảnh 9 không phải cảnh minh họa"), worker_id="w1")
    with SessionLocal() as db:
        video = db.get(Video, world.video)
        assert video.status == MediaStatus.failed and video.error == "Cảnh 9 không phải cảnh minh họa"


def test_a_system_failure_shows_the_safe_message(world):
    with SessionLocal() as db:
        db.get(Job, world.job).max_attempts = 1
        db.get(Video, world.video).error = "an older error"
        db.commit()
    handle_job_error(world.job, RuntimeError("upstream 500 at 10.0.0.7"), worker_id="w1")
    with SessionLocal() as db:
        assert db.get(Video, world.video).error == SYSTEM_FAILURE_MESSAGE


@pytest.mark.parametrize("payload", [{}, {"video_id": "not-a-uuid"}])
def test_a_failed_job_without_a_usable_video_id_still_fails_cleanly(world, payload):
    with SessionLocal() as db:
        db.get(Job, world.job).payload = payload
        db.commit()
    handle_job_error(world.job, ValueError("x"), worker_id="w1")
    with SessionLocal() as db:
        assert db.get(Job, world.job).status == JobStatus.failed
        assert db.get(Video, world.video).status == MediaStatus.draft


def test_a_video_deleted_while_a_scene_failed_ends_the_job_done(world, monkeypatch, tmp_path):
    def fake(alias, prompt, **kw):
        if "Gantt" in prompt:
            raise MediaError("returned no image")
        with SessionLocal() as db:
            db.query(Video).filter(Video.id == world.video).delete()
            db.commit()
        return GeneratedImage(_png(), "image/png", "x", None)

    monkeypatch.setattr(illustrations, "generate_image", fake)
    monkeypatch.setattr(illustrations, "PARALLEL_DRAWINGS", 1)
    with SessionLocal() as db:
        job = db.get(Job, world.job)
        db.expunge(job)
    run_claimed_job(job, "w1")
    with SessionLocal() as db:
        assert db.get(Job, world.job).status == JobStatus.done
    assert _files(tmp_path) == []


def test_files_of_a_scene_removed_while_drawing_are_deleted_when_the_job_ends(world, monkeypatch, tmp_path):
    from app.storage import media_store
    removed_voice = f"videos/{world.video}/voice-1-old.wav"
    with SessionLocal() as db:
        video = db.get(Video, world.video)
        video.scenes = [video.scenes[0], {**video.scenes[1], "voice_key": removed_voice}, video.scenes[2]]
        db.commit()
    path = media_store().path(removed_voice)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"old voice")

    def fake(alias, prompt, **kw):
        # The lecturer removes the title scene while the pictures are drawn; the save leaves its
        # file alone, since this job read the scenes before.
        with SessionLocal() as db:
            video = db.get(Video, world.video)
            if len(video.scenes) == 3:
                video.scenes = [video.scenes[0], video.scenes[2]]
                db.commit()
        return GeneratedImage(_png(), "image/png", "x", None)

    monkeypatch.setattr(illustrations, "generate_image", fake)
    with SessionLocal() as db:
        execute_job_step(db.get(Job, world.job))

    assert not path.exists()
    with SessionLocal() as db:
        assert db.get(Job, world.job).status == JobStatus.done
