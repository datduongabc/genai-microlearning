import json
import shutil
import uuid
from typing import ClassVar

import pytest

from app.db import SessionLocal
from app.generation.video_script import Scene
from app.jobs.failures import SYSTEM_FAILURE_MESSAGE
from app.jobs.queue import enqueue_job, execute_job_step, handle_job_error
from app.llm.media import Speech
from app.models import Chunk, Course, Document, DocumentStatus, Job, JobStatus, JobType, MediaStatus, User, Video
from app.video import render


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_storage_dir", str(tmp_path))
    with SessionLocal() as db:
        user = User(email=f"vr-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Render", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="x.pdf", status=DocumentStatus.embedded)
        db.add(doc)
        db.flush()
        chunk = Chunk(document_id=doc.id, course_id=course.id, chunk_index=0, content="FCFS.",
                      source_ref=json.dumps({"page": 7}))
        db.add(chunk)
        db.flush()
        scenes = [Scene(kind="summary", title=f"Cảnh {i}", bullets=["a", "b"], narration=f"Câu {i}.",
                        citations=[str(chunk.id)]).model_dump() for i in range(2)]
        video = Video(course_id=course.id, document_id=doc.id, focus="FCFS", output_language="vi",
                      model_alias="gemini-fast", voice_alias="voice-heygen", image_alias="image-nano-banana",
                      scenes=scenes, status=MediaStatus.draft, draft_version=2, error="an old error")
        db.add(video)
        db.flush()
        job = enqueue_job(db, job_type=JobType.video_render, step="render", document_id=doc.id,
                          payload={"video_id": str(video.id)})
        job.status, job.locked_by = JobStatus.running, "w1"
        db.commit()
        ids = (user.id, course.id, video.id, job.id)
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids[1]).delete()
        db.query(User).filter(User.id == ids[0]).delete()
        db.commit()


class _Slides:
    labels: ClassVar[list[str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def render(self, scene, image, source_label, size=(1280, 720), lang="vi"):
        from io import BytesIO

        from PIL import Image
        _Slides.labels.append(source_label)
        out = BytesIO()
        Image.new("RGB", size, "white").save(out, "PNG")
        return out.getvalue()


def test_the_job_renders_the_video_and_finishes(world, monkeypatch):
    _, _, video_id, job_id = world
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    monkeypatch.setattr(render, "FFMPEG", ffmpeg)
    monkeypatch.setattr(render, "SlideRenderer", _Slides)
    monkeypatch.setattr(render, "synthesize_speech", lambda alias, text, **kw: Speech(
        b"\x00\x00" * 24_000, 24_000, "heygen/heygen-voice-1-turbo", 0.001))
    _Slides.labels = []
    with SessionLocal() as db:
        execute_job_step(db.get(Job, job_id))
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        assert video.status == MediaStatus.ready and video.error is None
        assert video.rendered_version == 2 and video.video_key.endswith(".mp4")
        assert video.duration_seconds == pytest.approx(2 * (1 + render.SCENE_PAD_SECONDS), abs=0.05)
        assert all(s["voice_key"] for s in video.scenes)
        assert db.get(Job, job_id).status == JobStatus.done
        # What learners are shown of the render: each scene starts where the one before it ended.
        length = 1 + render.SCENE_PAD_SECONDS
        assert [s["title"] for s in video.rendered_scenes] == ["Cảnh 0", "Cảnh 1"]
        assert [s["start_seconds"] for s in video.rendered_scenes] == [pytest.approx(0, abs=0.05), pytest.approx(length, abs=0.05)]
        assert all(len(s["citations"]) == 1 for s in video.rendered_scenes)
    assert _Slides.labels == ["Nguồn: x.pdf, trang 7"] * 2


def test_a_render_that_runs_out_of_attempts_marks_the_video_failed(world):
    _, _, video_id, job_id = world
    with SessionLocal() as db:
        db.get(Job, job_id).max_attempts = 1
        db.commit()
    handle_job_error(job_id, RuntimeError("ffmpeg exited with 1"), worker_id="w1")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        assert video.status == MediaStatus.failed and video.error == SYSTEM_FAILURE_MESSAGE
        assert video.video_key is None


def test_a_render_that_names_its_scenes_keeps_its_own_error(world):
    _, _, video_id, job_id = world
    long_error = "Không tạo được giọng cho " + "cảnh 1 (Một tiêu đề dài), " * 12
    with SessionLocal() as db:
        db.get(Video, video_id).error = long_error
        db.commit()
    handle_job_error(job_id, ValueError(long_error), worker_id="w1")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        assert video.status == MediaStatus.failed and video.error == long_error


def test_files_of_a_scene_removed_while_rendering_are_deleted_even_when_the_render_fails(world, monkeypatch, tmp_path):
    from app.llm.media import MediaError
    from app.storage import media_store
    _, _, video_id, job_id = world
    removed_image = f"videos/{video_id}/img-1-old.jpg"
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.scenes = [video.scenes[0], {**video.scenes[1], "image_key": removed_image}]
        db.commit()
    path = media_store().path(removed_image)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"old picture")

    def no_voice(alias, text, **kw):
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            if len(video.scenes) == 2:
                video.scenes = [video.scenes[0]]
                db.commit()
        raise MediaError("no audio")

    monkeypatch.setattr(render, "synthesize_speech", no_voice)
    with SessionLocal() as db, pytest.raises(ValueError):
        execute_job_step(db.get(Job, job_id))

    assert not path.exists()
