"""The render job, with fake voices (silent PCM) and a real ffmpeg.

The slides are flat colours from a fake renderer, so a frame grabbed from the MP4 tells which
scene is on screen; one test renders real slides, the way the worker does.
"""

import io
import re
import shutil
import subprocess
import time
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from PIL import Image

from app.generation.video_script import Scene, normalize_narration
from app.jobs.queue import LostJob
from app.llm.media import VOICES, MediaError, Speech
from app.models import MediaStatus
from app.storage import media_store
from app.video import render
from app.video.render import render_video

ALIAS = "voice-heygen"
SLUG = VOICES[ALIAS].slug
PAD = render.SCENE_PAD_SECONDS
COLOURS = [(220, 40, 40), (40, 160, 60), (40, 70, 200), (230, 180, 30), (150, 50, 170),
           (20, 170, 170), (240, 120, 20), (90, 90, 90), (200, 80, 140), (60, 130, 230)]


def _ffmpeg() -> str:
    # The image has ffmpeg on PATH; a host without it uses the binary of the imageio-ffmpeg dev dependency.
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


FFMPEG = _ffmpeg()


@pytest.fixture(autouse=True)
def ffmpeg(monkeypatch):
    monkeypatch.setattr(render, "FFMPEG", FFMPEG)


class FakeDb:
    """A video row that `gone` removes, as a document deleted while the job renders would."""

    expire_on_commit = True

    def __init__(self, video, on_commit=None):
        self.video = video
        self.gone = False
        self.commits = 0
        self.rollbacks = 0
        self.on_commit = on_commit

    def get(self, model, _id, **kw):
        return None if self.gone else self.video

    def commit(self):
        self.commits += 1
        if self.on_commit is not None:
            self.on_commit(self)

    def rollback(self):
        self.rollbacks += 1


def _narration(i: int) -> str:
    return f"Cảnh số {i} nói về FCFS.\nCâu thứ hai của cảnh {i}."


def _scene(i, **kw) -> dict:
    fields = {"kind": "summary", "title": f"Cảnh {i}", "bullets": ["a", "b"], "narration": _narration(i),
              "citations": [], **kw}
    return Scene(**fields).model_dump()


def _video(n=3, **kw):
    fields = {"id": uuid.uuid4(), "document_id": uuid.uuid4(), "voice_alias": ALIAS, "output_language": "vi",
              "scenes": [_scene(i) for i in range(n)], "status": MediaStatus.rendering, "error": None,
              "cost": {"script": {"usd": None}}, "served_models": [], "draft_version": 3,
              "rendered_version": None, "video_key": None, "duration_seconds": None, **kw}
    return SimpleNamespace(**fields)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_storage_dir", str(tmp_path))
    return media_store()


def _stored(tmp_path, pattern="*") -> list[str]:
    root = tmp_path / "media"
    return sorted(str(p.relative_to(root)) for p in root.rglob(pattern) if p.is_file()) if root.exists() else []


class FakeSlides:
    """A renderer whose slide is a flat colour per scene, named in the scene's title."""

    opened = 0
    calls: ClassVar[list] = []

    def __enter__(self):
        FakeSlides.opened += 1
        return self

    def __exit__(self, *exc):
        return None

    def render(self, scene, image, source_label, size=(1280, 720), lang="vi"):
        FakeSlides.calls.append((scene.title, image, source_label, size, lang))
        index = int(re.search(r"\d+", scene.title)[0])
        out = io.BytesIO()
        Image.new("RGB", size, COLOURS[index % len(COLOURS)]).save(out, "PNG")
        return out.getvalue()


@pytest.fixture
def slides(monkeypatch):
    FakeSlides.opened = 0
    FakeSlides.calls = []
    monkeypatch.setattr(render, "SlideRenderer", FakeSlides)
    monkeypatch.setattr(render, "scene_source_labels", lambda db, video, scenes: [
        f"Nguồn: x.pdf, trang {i + 1}" for i in range(len(scenes))])
    return FakeSlides


def _fake_voices(monkeypatch, seconds=None, rate=44_100, fail=None, cost=0.002):
    """synthesize_speech that answers silence; `seconds(text)` and `rate(text)` may depend on the text."""
    calls = []

    def fake(alias, text, **kw):
        calls.append((alias, text))
        error = fail(text) if fail else None
        if error is not None:
            raise error
        r = rate(text) if callable(rate) else rate
        s = seconds(text) if callable(seconds) else (seconds or 1.0)
        return Speech(b"\x00\x00" * round(s * r), r, SLUG if alias == ALIAS else VOICES[alias].slug, cost)

    monkeypatch.setattr(render, "synthesize_speech", fake)
    return calls


def _probe(path) -> dict:
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, check=False).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()
    return {"duration": int(h) * 3600 + int(m) * 60 + float(s), "text": out}


def _frame_at(path, t) -> tuple[int, int, int]:
    png = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(png)).convert("RGB").getpixel((640, 360))


def _near(a, b, tolerance=24) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=True))


def _video_path(store, key) -> Path:
    return store.path(key)


def test_a_video_has_one_segment_per_scene_and_the_measured_duration(monkeypatch, store, slides, tmp_path):
    lengths = [1.0, 1.5, 2.0]
    _fake_voices(monkeypatch, seconds=lambda text: lengths[int(re.search(r"\d+", text)[0])])
    video = _video(3)
    render_video(FakeDb(video), video.id)

    assert video.status == MediaStatus.ready and video.error is None
    assert re.fullmatch(rf"videos/{video.id}/video-3-[0-9a-f]{{12}}\.mp4", video.video_key)
    assert video.rendered_version == 3
    # Each scene is its voice and the pad, rounded up to a whole frame.
    expected = sum(lengths) + PAD * len(lengths)
    assert expected <= video.duration_seconds <= expected + len(lengths) / render.FPS

    path = _video_path(store, video.video_key)
    probe = _probe(path)
    assert probe["duration"] == pytest.approx(video.duration_seconds, abs=0.05)
    assert re.search(r"Video: h264.*1280x720.*24 fps", probe["text"])
    assert re.search(r"Audio: aac.*44100 Hz", probe["text"])
    # Each scene's slide is on screen for its voice and the pad after it, in order.
    start = 0.0
    for i, length in enumerate(lengths):
        assert _near(_frame_at(path, start + 0.2), COLOURS[i]), f"scene {i} at its start"
        assert _near(_frame_at(path, start + length + PAD - 0.15), COLOURS[i]), f"scene {i} through its pad"
        start += length + PAD
    # One browser for every slide, each with its source line.
    assert slides.opened == 1
    assert [c[2] for c in slides.calls] == ["Nguồn: x.pdf, trang 1", "Nguồn: x.pdf, trang 2", "Nguồn: x.pdf, trang 3"]
    assert all(c[3] == (1280, 720) and c[4] == "vi" for c in slides.calls)


def test_each_voice_is_stored_as_a_wav_under_a_name_of_its_own(monkeypatch, store, slides, tmp_path):
    calls = _fake_voices(monkeypatch, seconds=0.5, rate=24_000)
    video = _video(2)
    render_video(FakeDb(video), video.id)
    for i, scene in enumerate(video.scenes):
        assert re.fullmatch(rf"videos/{video.id}/voice-{i}-[0-9a-f]{{12}}\.wav", scene["voice_key"])
        assert scene["voice_text_used"] == normalize_narration(scene["narration"], "vi")
        assert scene["voice_alias_used"] == ALIAS
        with wave.open(str(store.path(scene["voice_key"]))) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (24_000, 1, 2)
            assert w.getnframes() == 12_000
    # The voice reads the normalized narration, its sentences in one text.
    assert calls[0] == (ALIAS, normalize_narration(_narration(0), "vi").replace("\n", " "))
    assert _stored(tmp_path) == sorted([*(s["voice_key"] for s in video.scenes), video.video_key])


def test_unchanged_scenes_reuse_their_voice(monkeypatch, store, slides, tmp_path):
    calls = _fake_voices(monkeypatch)
    video = _video(3)
    db = FakeDb(video)
    render_video(db, video.id)
    assert len(calls) == 3
    first_keys = [s["voice_key"] for s in video.scenes]
    first_video = video.video_key

    edited = dict(video.scenes[1], narration="Lời mới của giảng viên.\nThêm một câu.")
    video.scenes = [video.scenes[0], edited, video.scenes[2]]
    video.draft_version = 4
    calls.clear()
    render_video(db, video.id)

    assert calls == [(ALIAS, "Lời mới của giảng viên. Thêm một câu.")]
    keys = [s["voice_key"] for s in video.scenes]
    assert keys[0] == first_keys[0] and keys[2] == first_keys[2] and keys[1] != first_keys[1]
    assert video.rendered_version == 4 and video.video_key != first_video
    # The replaced voice and the replaced video are gone; nothing else is left behind.
    assert _stored(tmp_path) == sorted([*keys, video.video_key])
    assert video.cost["voice"]["characters"] == sum(
        len(normalize_narration(_narration(i)).replace("\n", " ")) for i in range(3)) + len(calls[0][1])


def test_changing_the_voice_revoices_every_scene(monkeypatch, store, slides, tmp_path):
    calls = _fake_voices(monkeypatch)
    video = _video(2)
    db = FakeDb(video)
    render_video(db, video.id)
    video.voice_alias = "voice-gemini-lite"
    calls.clear()
    render_video(db, video.id)
    assert [c[0] for c in calls] == ["voice-gemini-lite", "voice-gemini-lite"]
    assert all(s["voice_alias_used"] == "voice-gemini-lite" for s in video.scenes)
    assert len(_stored(tmp_path, "voice-*")) == 2


def test_a_voice_whose_file_is_missing_is_made_again(monkeypatch, store, slides, tmp_path):
    calls = _fake_voices(monkeypatch)
    video = _video(2)
    db = FakeDb(video)
    render_video(db, video.id)
    store.delete(video.scenes[0]["voice_key"])
    calls.clear()
    render_video(db, video.id)
    assert len(calls) == 1 and store.size(video.scenes[0]["voice_key"])


def test_24_and_44_1_khz_voices_mix_in_one_video(monkeypatch, store, slides):
    _fake_voices(monkeypatch, seconds=1.2, rate=lambda text: 24_000 if "số 0" in text else 44_100)
    video = _video(2)
    render_video(FakeDb(video), video.id)
    probe = _probe(_video_path(store, video.video_key))
    assert 2 * (1.2 + PAD) <= video.duration_seconds <= 2 * (1.2 + PAD) + 2 / render.FPS
    assert probe["duration"] == pytest.approx(video.duration_seconds, abs=0.05)
    assert re.search(r"Audio: aac.*44100 Hz", probe["text"])


def test_a_measured_duration_over_eight_minutes_fails_and_keeps_the_script(monkeypatch, store, slides, tmp_path):
    # Short narration passes the estimate; the voice turns out slow.
    _fake_voices(monkeypatch, seconds=241.0, rate=8_000)
    video = _video(2)
    narrations = [s["narration"] for s in video.scenes]
    before_save = []
    with pytest.raises(ValueError) as failure:
        render_video(FakeDb(video), video.id, before_save=before_save.append)
    assert video.status == MediaStatus.failed
    assert "8 phút" in video.error and "8,1" in video.error
    assert str(failure.value) == video.error and before_save == [video.error]
    assert [s["narration"] for s in video.scenes] == narrations
    assert video.video_key is None and video.rendered_version is None and video.duration_seconds is None
    # No video was assembled; the voices are kept for when the script is shortened.
    assert slides.opened == 0
    assert _stored(tmp_path, "*.mp4") == []
    assert all(s["voice_key"] for s in video.scenes)


def test_blocking_problems_fail_at_once_without_a_paid_call(monkeypatch, store, slides):
    calls = _fake_voices(monkeypatch)
    video = _video(2)
    video.scenes = [video.scenes[0], _scene(1, kind="illustration", image_prompt="A queue")]
    before_save = []
    with pytest.raises(ValueError, match="Cảnh 2: chưa vẽ hình minh họa"):
        render_video(FakeDb(video), video.id, before_save=before_save.append)
    assert calls == [] and slides.opened == 0
    assert video.status == MediaStatus.failed and "Cảnh 2: chưa vẽ hình minh họa" in video.error
    assert before_save == [video.error]


def test_a_lost_picture_fails_at_once_and_says_so(monkeypatch, store, slides):
    calls = _fake_voices(monkeypatch)
    video = _video(2)
    video.scenes = [video.scenes[0], _scene(1, kind="illustration", image_prompt="A queue",
                                            image_key="videos/v/img-1-gone.jpg", image_prompt_used="A queue")]
    before_save = []
    with pytest.raises(ValueError, match="Cảnh 2: hình minh họa bị mất, hãy vẽ lại"):
        render_video(FakeDb(video), video.id, before_save=before_save.append)
    # Terminal and before any paid call: a retry would find the file missing again.
    assert calls == [] and slides.opened == 0
    assert video.status == MediaStatus.failed and "hình minh họa bị mất" in video.error
    assert before_save == [video.error]


def _save_old_video(store, tmp_path, video) -> str:
    old = f"videos/{video.id}/video-2-000000000000.mp4"
    source = tmp_path / "old.mp4"
    source.write_bytes(b"old video")
    store.save(source, old)
    video.video_key, video.rendered_version, video.duration_seconds = old, 2, 9.0
    return old


@pytest.mark.parametrize("where", ["ffmpeg", "store"])
def test_the_previous_video_stays_until_the_new_one_is_stored(monkeypatch, store, slides, tmp_path, where):
    """Review Focus 1: a render that fails partway leaves learners the video they had."""
    _fake_voices(monkeypatch)
    video = _video(2, status=MediaStatus.rendering)
    old = _save_old_video(store, tmp_path, video)
    if where == "ffmpeg":
        monkeypatch.setattr(render, "FFMPEG", str(tmp_path / "no-such-ffmpeg"))
    else:
        real_save = type(store).save

        def save(self, local_path, key):
            if key.endswith(".mp4"):
                raise OSError("disk full")
            real_save(self, local_path, key)

        monkeypatch.setattr(type(store), "save", save)
    with pytest.raises(Exception):  # noqa: B017 - what fails is the point, not its type
        render_video(FakeDb(video), video.id)
    assert video.video_key == old and video.rendered_version == 2 and video.duration_seconds == 9.0
    assert store.size(old) == len(b"old video")
    # The voices are paid for: they stay attached for the next try.
    assert all(s["voice_key"] for s in video.scenes)
    assert _stored(tmp_path, "*.mp4") == [old]


def test_the_previous_video_is_deleted_only_after_the_new_key_is_committed(monkeypatch, store, slides, tmp_path):
    _fake_voices(monkeypatch)
    video = _video(2)
    old = _save_old_video(store, tmp_path, video)
    db = FakeDb(video)
    deleted_at = []
    real_delete = type(store).delete
    monkeypatch.setattr(type(store), "delete", lambda self, key: deleted_at.append(
        (key, db.commits, video.video_key)) or real_delete(self, key))
    render_video(db, video.id)
    assert [d[0] for d in deleted_at] == [old]
    _, commits_then, key_then = deleted_at[0]
    assert key_then != old and commits_then == db.commits
    assert _stored(tmp_path, "*.mp4") == [video.video_key]


def test_a_failed_voice_keeps_the_others_and_names_the_scene(monkeypatch, store, slides, tmp_path):
    calls = _fake_voices(monkeypatch, fail=lambda text: MediaError("no audio") if "số 1" in text else None)
    video = _video(3)
    old = _save_old_video(store, tmp_path, video)
    before_save = []
    with pytest.raises(ValueError) as failure:
        render_video(FakeDb(video), video.id, before_save=before_save.append)
    assert video.status == MediaStatus.failed and str(failure.value) == video.error
    assert "cảnh 2 (Cảnh 1)" in video.error and VOICES[ALIAS].label in video.error
    assert "Cảnh 0" not in video.error
    assert video.scenes[0]["voice_key"] and video.scenes[2]["voice_key"] and video.scenes[1]["voice_key"] is None
    # A reply without audio is tried again once within the job.
    assert sum("số 1" in c[1] for c in calls) == render.VOICE_ATTEMPTS
    assert video.video_key == old and slides.opened == 0
    # Calls run in parallel, in any order; only the voices made are counted.
    assert video.cost["voice"]["characters"] == sum(len(text) for _, text in set(calls) if "số 1" not in text)
    assert [m["served_model"] for m in video.served_models] == [SLUG, SLUG]


def test_a_voice_refusal_names_the_data_policy(monkeypatch, store, slides):
    calls = _fake_voices(monkeypatch, fail=lambda text: ValueError(
        "HTTP 404: No endpoints found matching your data policy"))
    video = _video(1)
    with pytest.raises(ValueError):
        render_video(FakeDb(video), video.id)
    assert len(calls) == 1
    assert "không có nhà cung cấp nào" in video.error and "data_collection: deny" in video.error
    assert "model giọng khác" in video.error


def test_a_video_deleted_while_rendering_leaves_no_files(monkeypatch, store, slides, tmp_path):
    _fake_voices(monkeypatch)
    video = _video(2)
    db = FakeDb(video)
    finished = []

    def delete_meanwhile(error):
        db.gone = True
        finished.append(error)

    render_video(db, video.id, before_save=delete_meanwhile)
    assert finished == [None]
    # The voices were committed to the row, which the document's deletion cleans up; the new
    # video never reached the row, so the job deletes it.
    assert _stored(tmp_path, "*.mp4") == []


def test_a_video_deleted_before_its_voices_are_saved_leaves_no_files(monkeypatch, store, slides, tmp_path):
    _fake_voices(monkeypatch)
    video = _video(2)
    db = FakeDb(video)

    def delete_meanwhile():
        db.gone = True

    render_video(db, video.id, hold_job=delete_meanwhile)
    assert _stored(tmp_path) == [] and slides.opened == 0


def test_a_lost_job_writes_nothing_and_leaves_no_files(monkeypatch, store, slides, tmp_path):
    _fake_voices(monkeypatch)
    video = _video(2)

    def lost():
        raise LostJob("gone")

    with pytest.raises(LostJob):
        render_video(FakeDb(video), video.id, hold_job=lost)
    assert all(s["voice_key"] is None for s in video.scenes)
    assert _stored(tmp_path) == []


def test_narration_edited_while_voicing_does_not_get_the_old_voice(monkeypatch, store, slides, tmp_path):
    _fake_voices(monkeypatch)
    video = _video(2)

    def edit_meanwhile():
        video.scenes = [video.scenes[0], dict(video.scenes[1], narration="Sửa trong lúc dựng.")]
        video.draft_version = 4

    render_video(FakeDb(video), video.id, hold_job=edit_meanwhile)
    assert video.scenes[0]["voice_key"] and video.scenes[1]["voice_key"] is None
    # The video is the script as it was when the render started, and says so.
    assert video.rendered_version == 3 and video.status == MediaStatus.ready
    assert _stored(tmp_path, "voice-*") == [video.scenes[0]["voice_key"]]


def test_cost_and_served_models_are_added(monkeypatch, store, slides):
    _fake_voices(monkeypatch, cost=0.003)
    video = _video(2, cost={"script": {"usd": None}, "voice": {"usd": 0.01, "characters": 100, "estimated": True}},
                   served_models=[{"requested_alias": "x", "routed_alias": "x", "served_model": "m"}])
    render_video(FakeDb(video), video.id)
    assert video.cost["voice"]["usd"] == pytest.approx(0.016)
    assert video.cost["voice"]["estimated"] is True
    assert video.cost["script"] == {"usd": None}
    assert [m["served_model"] for m in video.served_models] == ["m", SLUG, SLUG]


def test_illustration_pictures_reach_their_slide(monkeypatch, store, slides, tmp_path):
    _fake_voices(monkeypatch)
    picture = tmp_path / "pic.jpg"
    Image.new("RGB", (40, 30), "white").save(picture, "JPEG")
    data = picture.read_bytes()
    store.save(picture, "videos/v/img-1-abc.jpg")
    video = _video(2)
    video.scenes = [video.scenes[0], _scene(1, kind="illustration", image_prompt="A queue",
                                            image_key="videos/v/img-1-abc.jpg", image_prompt_used="A queue")]
    render_video(FakeDb(video), video.id)
    assert slides.calls[0][1] is None and slides.calls[1][1] == data


def test_a_nine_scene_video_renders_with_real_slides(monkeypatch, store, tmp_path):
    """The worker's path: one Chromium for the nine slides, then ffmpeg. Also the timing the report quotes."""
    monkeypatch.setattr(render, "scene_source_labels", lambda db, video, scenes: [
        "Nguồn: Chapter 5_CPU Scheduling.pdf, trang 9–10"] * len(scenes))
    _fake_voices(monkeypatch, seconds=2.0, rate=lambda text: 24_000 if "số 3" in text else 44_100)
    video = _video(9)
    started = time.monotonic()
    render_video(FakeDb(video), video.id)
    elapsed = time.monotonic() - started
    path = _video_path(store, video.video_key)
    probe = _probe(path)
    assert probe["duration"] == pytest.approx(video.duration_seconds, abs=0.05)
    assert 9 * (2.0 + PAD) <= video.duration_seconds <= 9 * (2.0 + PAD + 1 / render.FPS)
    print(f"\n9-scene render: {elapsed:.1f} s, {path.stat().st_size} bytes, {probe['duration']:.2f} s, {path}")
