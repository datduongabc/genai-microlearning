import io
import re
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from app.generation.video_script import Scene
from app.jobs.queue import LostJob
from app.llm.media import IMAGE_MODELS, RETRY_WAIT_SECONDS, GeneratedImage, MediaError
from app.models import MediaStatus
from app.storage import media_store
from app.video import illustrations
from app.video.illustrations import IMAGE_PROMPT, draw_illustrations, image_prompt_for, trim_whitespace

ALIAS = "image-nano-banana"
SLUG = IMAGE_MODELS[ALIAS].slug


class FakeDb:
    """A video row that `gone` removes, as a document deleted while the job draws would."""

    expire_on_commit = True

    def __init__(self, video):
        self.video = video
        self.gone = False
        self.commits = 0
        self.rollbacks = 0

    def get(self, model, _id, **kw):
        return None if self.gone else self.video

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _drawing(width=1024, height=768, box=(300, 250, 700, 520), color=(19, 41, 75)) -> bytes:
    im = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(im).rectangle(box, fill=color)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def _scene(title, kind="illustration", prompt="A queue with P1, P2 and P3", used=None, key=None) -> dict:
    return Scene(kind=kind, title=title, bullets=["a", "b"], narration="Một câu.", citations=[],
                 image_prompt=prompt if kind == "illustration" else None, image_prompt_used=used,
                 image_key=key).model_dump()


def _video(scenes):
    return SimpleNamespace(id=uuid.uuid4(), image_alias=ALIAS, scenes=scenes, status=MediaStatus.drawing,
                           error=None, cost={"script": {"usd": None}}, served_models=[])


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_storage_dir", str(tmp_path))
    return media_store()


def _stored_files(tmp_path) -> list[str]:
    return sorted(str(p.relative_to(tmp_path / "media")) for p in (tmp_path / "media").rglob("*") if p.is_file())


def _fake_images(monkeypatch, fail=None, cost=0.04):
    """generate_image that draws a picture for each prompt, or raises what `fail(prompt)` returns."""
    calls = []

    def fake(alias, prompt, aspect_ratio="4:3", **kw):
        calls.append((alias, prompt, aspect_ratio))
        error = fail(prompt) if fail else None
        if error is not None:
            raise error
        # A different picture per call, as a model draws: the key is named after its content.
        return GeneratedImage(_drawing(color=(len(calls) * 20 % 255, 41, 75)), "image/png", SLUG, cost)

    monkeypatch.setattr(illustrations, "generate_image", fake)
    return calls


def test_the_prompt_asks_for_the_picture_only():
    scene = Scene.model_validate(_scene("Hàng đợi", prompt="A queue with P1, P2 and P3"))
    prompt = image_prompt_for(scene)
    assert "A queue with P1, P2 and P3" in prompt
    assert prompt == IMAGE_PROMPT.format(description="A queue with P1, P2 and P3")
    wrapper = IMAGE_PROMPT.lower()
    assert "no text" in wrapper and "labels" in wrapper and "the description names" in wrapper
    assert "plain" in wrapper and "light background" in wrapper
    assert "empty space" in wrapper


def test_trim_keeps_a_margin_around_the_drawing():
    trimmed = Image.open(io.BytesIO(trim_whitespace(_drawing(box=(300, 250, 699, 519))))).convert("RGB")
    margin = illustrations.TRIM_MARGIN
    assert trimmed.size == (400 + 2 * margin, 270 + 2 * margin)
    assert trimmed.getpixel((margin - 1, margin - 1)) == (255, 255, 255)
    assert trimmed.getpixel((margin + 1, margin + 1)) == (19, 41, 75)


def test_trim_stops_at_the_edge_of_the_picture():
    trimmed = Image.open(io.BytesIO(trim_whitespace(_drawing(box=(10, 300, 900, 500)))))
    assert trimmed.size == (901 + illustrations.TRIM_MARGIN, 201 + 2 * illustrations.TRIM_MARGIN)


def test_a_picture_that_fills_the_frame_is_left_whole():
    full = _drawing(box=(0, 0, 1023, 767))
    assert Image.open(io.BytesIO(trim_whitespace(full))).size == (1024, 768)


def test_only_changed_prompts_are_redrawn(monkeypatch, store, tmp_path):
    old = "videos/v/img-0-old.jpg"
    video = _video([
        _scene("Không đổi", prompt="Same", used="Same", key=old),
        _scene("Đã sửa", prompt="A Gantt chart with P1 from 0 to 24", used="A Gantt chart", key="videos/v/img-1-x.jpg"),
        _scene("Mở đầu", kind="title"),
    ])
    calls = _fake_images(monkeypatch)
    draw_illustrations(FakeDb(video), video.id)
    assert len(calls) == 1
    assert "A Gantt chart with P1 from 0 to 24" in calls[0][1] and calls[0][2] == "4:3"
    first, second, _ = video.scenes
    assert first["image_key"] == old
    assert second["image_prompt_used"] == "A Gantt chart with P1 from 0 to 24"
    assert re.fullmatch(rf"videos/{video.id}/img-1-[0-9a-f]{{12}}\.jpg", second["image_key"])
    assert video.status == MediaStatus.draft and video.error is None


def test_a_scene_never_drawn_is_drawn(monkeypatch, store):
    video = _video([_scene("Mới")])
    calls = _fake_images(monkeypatch)
    draw_illustrations(FakeDb(video), video.id)
    assert len(calls) == 1 and video.scenes[0]["image_key"]


def test_listed_scenes_are_redrawn_even_when_unchanged(monkeypatch, store):
    video = _video([_scene("A", used="A queue with P1, P2 and P3", key="videos/v/a.jpg"),
                    _scene("B", used="A queue with P1, P2 and P3", key="videos/v/b.jpg")])
    calls = _fake_images(monkeypatch)
    draw_illustrations(FakeDb(video), video.id, scene_indexes=[1])
    assert len(calls) == 1
    assert video.scenes[0]["image_key"] == "videos/v/a.jpg"
    assert video.scenes[1]["image_key"] != "videos/v/b.jpg"


@pytest.mark.parametrize("indexes", [[5], [1], [-1]])
def test_a_listed_scene_that_cannot_be_drawn_is_refused_before_any_call(monkeypatch, store, indexes):
    video = _video([_scene("A"), _scene("B", kind="summary")])
    calls = _fake_images(monkeypatch)
    with pytest.raises(ValueError):
        draw_illustrations(FakeDb(video), video.id, scene_indexes=indexes)
    assert calls == []


def test_the_stored_image_is_a_trimmed_jpeg(monkeypatch, store, tmp_path):
    video = _video([_scene("A")])
    _fake_images(monkeypatch)
    draw_illustrations(FakeDb(video), video.id)
    key = video.scenes[0]["image_key"]
    assert _stored_files(tmp_path) == [key]
    with store.local_copy(key) as path:
        im = Image.open(path)
        assert im.format == "JPEG"
        assert im.size == (400 + 1 + 2 * illustrations.TRIM_MARGIN, 270 + 1 + 2 * illustrations.TRIM_MARGIN)


def test_the_old_image_is_deleted_only_after_the_new_key_is_committed(monkeypatch, store, tmp_path):
    source = tmp_path / "old"
    source.write_bytes(b"old")
    store.save(source, "videos/v/img-0-old.jpg")
    video = _video([_scene("A", used="before", key="videos/v/img-0-old.jpg")])
    db = FakeDb(video)
    _fake_images(monkeypatch)
    deleted_at = []
    real_delete = type(store).delete
    monkeypatch.setattr(type(store), "delete", lambda self, key: deleted_at.append((key, db.commits, video.scenes[0]["image_key"])) or real_delete(self, key))
    draw_illustrations(db, video.id)
    assert len(deleted_at) == 1
    key, commits, key_then = deleted_at[0]
    assert key == "videos/v/img-0-old.jpg" and commits >= 1 and key_then != key
    assert _stored_files(tmp_path) == [video.scenes[0]["image_key"]]


def test_cost_and_served_models_are_added(monkeypatch, store):
    video = _video([_scene("A"), _scene("B", prompt="A Gantt chart")])
    video.cost["images"] = {"usd": 0.1, "images": 2}
    _fake_images(monkeypatch, cost=0.04)
    draw_illustrations(FakeDb(video), video.id)
    assert video.cost["images"] == {"usd": pytest.approx(0.18), "images": 4}
    assert video.cost["script"] == {"usd": None}
    assert [m["served_model"] for m in video.served_models] == [SLUG, SLUG]


def test_a_reply_without_an_image_keeps_the_prompt_and_fails_the_job_clearly(monkeypatch, store, tmp_path):
    """Review Focus 2: the scene that got no image keeps its description and its old picture, the
    other scene's new picture is kept, and the error names the scene."""
    video = _video([
        _scene("Hàng đợi sẵn sàng", prompt="A ready queue", used="old", key="videos/v/img-0-old.jpg"),
        _scene("Biểu đồ Gantt", prompt="A Gantt chart"),
    ])
    calls = _fake_images(monkeypatch, fail=lambda p: MediaError("returned no image") if "ready queue" in p else None)
    before_save = []
    with pytest.raises(ValueError) as failure:
        draw_illustrations(FakeDb(video), video.id, before_save=before_save.append)
    first, second = video.scenes
    assert first["image_prompt"] == "A ready queue"
    assert first["image_key"] == "videos/v/img-0-old.jpg" and first["image_prompt_used"] == "old"
    assert second["image_key"] and second["image_prompt_used"] == "A Gantt chart"
    assert video.status == MediaStatus.failed
    assert "cảnh 1" in video.error and "Hàng đợi sẵn sàng" in video.error
    assert "Biểu đồ Gantt" not in video.error
    assert str(failure.value) == video.error
    # The job is told it fails, so it is not marked done in the transaction that saves.
    assert before_save == [video.error]
    # A reply with no image is tried again once within the job, never by requeueing the whole job.
    assert sum("ready queue" in c[1] for c in calls) == illustrations.IMAGE_ATTEMPTS
    assert _stored_files(tmp_path) == [second["image_key"]]


def test_a_second_try_that_gets_an_image_succeeds(monkeypatch, store):
    tries = []
    video = _video([_scene("A")])
    _fake_images(monkeypatch, fail=lambda p: tries.append(p) or (MediaError("empty") if len(tries) == 1 else None))
    draw_illustrations(FakeDb(video), video.id)
    assert video.scenes[0]["image_key"] and video.status == MediaStatus.draft


def test_a_busy_provider_is_waited_for_before_the_second_try(monkeypatch, store, media_waits):
    tries = []
    video = _video([_scene("A")])
    _fake_images(monkeypatch, fail=lambda p: tries.append(p) or (
        MediaError("429", retry_after=9) if len(tries) == 1 else None))

    draw_illustrations(FakeDb(video), video.id)

    assert media_waits == [9]
    assert video.scenes[0]["image_key"] and video.status == MediaStatus.draft


def test_the_last_try_is_not_waited_after(monkeypatch, store, media_waits):
    video = _video([_scene("A")])
    _fake_images(monkeypatch, fail=lambda p: MediaError("503"))

    with pytest.raises(ValueError):
        draw_illustrations(FakeDb(video), video.id)

    assert media_waits == [RETRY_WAIT_SECONDS] * (illustrations.IMAGE_ATTEMPTS - 1)


def test_a_provider_refusal_names_the_model(monkeypatch, store):
    video = _video([_scene("Biểu đồ Gantt")])
    calls = _fake_images(monkeypatch, fail=lambda p: ValueError(
        "HTTP 404: No endpoints found matching your data policy"))
    with pytest.raises(ValueError):
        draw_illustrations(FakeDb(video), video.id)
    assert len(calls) == 1  # a refusal repeats, so it is not tried again
    assert IMAGE_MODELS[ALIAS].label in video.error and "Biểu đồ Gantt" in video.error
    assert "không có nhà cung cấp nào" in video.error and "không thu thập dữ liệu" in video.error


def test_another_refusal_quotes_the_provider(monkeypatch, store):
    video = _video([_scene("Biểu đồ Gantt")])
    _fake_images(monkeypatch, fail=lambda p: ValueError("HTTP 400: prompt rejected by safety filter"))
    with pytest.raises(ValueError):
        draw_illustrations(FakeDb(video), video.id)
    assert IMAGE_MODELS[ALIAS].label in video.error and "safety filter" in video.error
    assert video.scenes[0]["image_key"] is None


def test_a_video_deleted_while_drawing_leaves_no_files(monkeypatch, store, tmp_path):
    video = _video([_scene("A"), _scene("B", prompt="other")])
    db = FakeDb(video)
    _fake_images(monkeypatch)
    finished = []

    def delete_meanwhile(error):
        db.gone = True
        finished.append(error)

    draw_illustrations(db, video.id, before_save=delete_meanwhile)
    assert finished == [None]
    assert _stored_files(tmp_path) == []


def test_a_lost_job_writes_nothing_and_leaves_no_files(monkeypatch, store, tmp_path):
    video = _video([_scene("A")])
    _fake_images(monkeypatch)

    def lost(_error):
        raise LostJob("gone")

    with pytest.raises(LostJob):
        draw_illustrations(FakeDb(video), video.id, before_save=lost)
    assert video.scenes[0]["image_key"] is None
    assert _stored_files(tmp_path) == []


def test_a_description_edited_while_drawing_does_not_get_the_old_picture(monkeypatch, store, tmp_path):
    video = _video([_scene("A", prompt="first")])

    def edit_meanwhile(_error):
        video.scenes = [_scene("A", prompt="edited by the lecturer")]

    _fake_images(monkeypatch)
    draw_illustrations(FakeDb(video), video.id, before_save=edit_meanwhile)
    assert video.scenes[0]["image_key"] is None
    assert video.scenes[0]["image_prompt"] == "edited by the lecturer"
    assert _stored_files(tmp_path) == []


def test_nothing_to_draw_makes_no_call(monkeypatch, store):
    video = _video([_scene("A", prompt="Same", used="Same", key="videos/v/a.jpg"), _scene("B", kind="summary")])
    calls = _fake_images(monkeypatch)
    draw_illustrations(FakeDb(video), video.id)
    assert calls == [] and video.status == MediaStatus.draft


def test_trim_keeps_a_pale_fill():
    """A pastel box differs from white in one channel only; its luminance alone would count it as
    background and cut it off."""
    pale = (255, 249, 196)
    trimmed = Image.open(io.BytesIO(trim_whitespace(_drawing(box=(300, 250, 699, 519), color=pale))))
    margin = illustrations.TRIM_MARGIN
    assert trimmed.size == (400 + 2 * margin, 270 + 2 * margin)
    assert trimmed.convert("RGB").getpixel((margin + 5, margin + 5)) == pale


def test_a_video_deleted_while_a_scene_failed_ends_the_job(monkeypatch, store, tmp_path):
    video = _video([_scene("A"), _scene("B", prompt="other")])
    db = FakeDb(video)
    _fake_images(monkeypatch, fail=lambda p: MediaError("empty") if p.endswith("other") else None)
    finished = []

    def delete_meanwhile(error):
        db.gone = True
        finished.append(error)

    # Nothing is left to show the failure on, so the job ends done, not running and not failed.
    draw_illustrations(db, video.id, before_save=delete_meanwhile)
    assert finished[-1] is None
    assert _stored_files(tmp_path) == []


@pytest.mark.parametrize("bad", ["undecodable", "crash"])
def test_one_bad_drawing_loses_no_other_picture(monkeypatch, store, bad):
    video = _video([_scene("Hàng đợi", prompt="A queue"), _scene("Biểu đồ Gantt", prompt="A Gantt")])

    def fake(alias, prompt, aspect_ratio="4:3", **kw):
        if "Gantt" in prompt:
            if bad == "crash":
                raise RuntimeError("unexpected")
            return GeneratedImage(b"not an image", "image/png", SLUG, 0.04)
        return GeneratedImage(_drawing(), "image/png", SLUG, 0.04)

    monkeypatch.setattr(illustrations, "generate_image", fake)
    with pytest.raises(ValueError):
        draw_illustrations(FakeDb(video), video.id)
    assert video.scenes[0]["image_key"]
    assert video.scenes[1]["image_key"] is None
    assert "cảnh 2 (Biểu đồ Gantt)" in video.error and "Hàng đợi" not in video.error


def test_a_failure_while_storing_deletes_the_files_already_stored(monkeypatch, store, tmp_path):
    video = _video([_scene("A", prompt="first"), _scene("B", prompt="second")])
    _fake_images(monkeypatch)
    real_save = type(store).save
    saved = []

    def save_then_fail(self, path, key):
        if saved:
            raise OSError("disk full")
        saved.append(key)
        real_save(self, path, key)

    monkeypatch.setattr(type(store), "save", save_then_fail)
    with pytest.raises(OSError):
        draw_illustrations(FakeDb(video), video.id)
    assert _stored_files(tmp_path) == []


def test_a_cleanup_that_fails_does_not_hide_the_error(monkeypatch, store):
    video = _video([_scene("A")])
    _fake_images(monkeypatch)
    monkeypatch.setattr(type(store), "delete", lambda self, key: (_ for _ in ()).throw(OSError("S3 down")))

    def lost(_error):
        raise LostJob("gone")

    with pytest.raises(LostJob):
        draw_illustrations(FakeDb(video), video.id, before_save=lost)


def test_a_delete_after_the_commit_that_fails_is_only_logged(monkeypatch, store, caplog):
    video = _video([_scene("A", used="before", key="videos/v/img-0-old.jpg")])
    _fake_images(monkeypatch)
    monkeypatch.setattr(type(store), "delete", lambda self, key: (_ for _ in ()).throw(OSError("S3 down")))
    draw_illustrations(FakeDb(video), video.id)
    assert video.scenes[0]["image_key"] != "videos/v/img-0-old.jpg"
    assert "videos/v/img-0-old.jpg" in caplog.text
