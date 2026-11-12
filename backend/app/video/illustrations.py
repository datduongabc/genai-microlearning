"""The pictures of a video's illustration scenes, drawn by an image model.

The model draws the picture only: the slide prints the title, the bullets and every word itself
(app/video/slides.py), because in the trial the image models misspelled Vietnamese and one drew
the wrong operation. Only scenes whose description changed since their picture was drawn are
drawn again, or the scenes the lecturer names, so an edit to one scene costs one picture.

When a picture cannot be drawn, the scene keeps its description and its old picture, the pictures
that were drawn are kept (they are paid for), and the job fails naming the scenes, for the
lecturer to draw again (Review Focus 2). A picture whose scene changed or vanished while it was
drawn is not attached to it, and its file is deleted.
"""

import hashlib
import io
import logging
import re
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PIL import Image, ImageChops, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.db import WorkerSessionLocal
from app.generation.media_common import values_kept_across_commits
from app.generation.video_script import MAX_ILLUSTRATIONS, Scene
from app.jobs.queue import hold
from app.llm.media import IMAGE_MODELS, MediaError, generate_image
from app.models import Job, JobStatus, MediaStatus, Video
from app.storage import media_store, staging_dir

logger = logging.getLogger(__name__)

# What the image model is asked, around the scene's description. The description already names
# every label the picture shows (app/generation/video_script.py); the slide prints the rest.
IMAGE_PROMPT = (
    "Draw the picture described below as one clean flat vector illustration for a lecture slide, "
    "on a plain white or very light background. Draw no text, letters, numbers or labels other than "
    "the ones the description names, and no title, caption or legend: the slide prints its own text "
    "beside the picture. Leave empty space around the drawing.\n\n"
    "Picture: {description}"
)
# The slide's picture column is about 4:3 (560 x 520 px less padding).
ASPECT_RATIO = "4:3"
# A reply without an image is often a one-off; a second try within the job is cheaper for the
# lecturer than a failed job, and a third is not worth the wait.
IMAGE_ATTEMPTS = 2
# Drawn at once: a video has at most MAX_ILLUSTRATIONS, each takes 10 to 30 seconds.
PARALLEL_DRAWINGS = MAX_ILLUSTRATIONS
# Air kept around the drawing when the empty background is trimmed, in pixels of the model's image.
TRIM_MARGIN = 40
# How far from the background a pixel must be to count as drawing: JPEG noise stays under it.
TRIM_THRESHOLD = 18
# Twice the size the slide shows it at, enough for a 1920x1080 render.
MAX_SIDE = 1600
JPEG_QUALITY = 90
_TITLE_CHARS = 60


def image_prompt_for(scene: Scene) -> str:
    return IMAGE_PROMPT.format(description=(scene.image_prompt or "").strip())


def trim_whitespace(image: bytes) -> bytes:
    """The picture without the empty background a model leaves around a thin drawing, keeping
    TRIM_MARGIN pixels of it, as a PNG. The background is the colour of the corners; a picture
    whose corners differ has no plain background and is left whole."""
    im = Image.open(io.BytesIO(image))
    im.load()
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        flat = Image.new("RGB", im.size, (255, 255, 255))
        flat.paste(im, mask=im.getchannel("A"))
        im = flat
    im = im.convert("RGB")
    w, h = im.size
    corners = [im.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    background = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
    box = None
    if all(max(abs(c[i] - background[i]) for i in range(3)) <= TRIM_THRESHOLD for c in corners):
        # The largest channel difference, not the luminance: a pale yellow differs from white in
        # blue alone, and luminance weighs blue so little that the fill would read as background.
        diff = ImageChops.difference(im, Image.new("RGB", im.size, background))
        mask = ImageChops.lighter(ImageChops.lighter(*diff.split()[:2]), diff.split()[2])
        box = mask.point(lambda v: 255 if v > TRIM_THRESHOLD else 0).getbbox()
    if box is not None:
        im = im.crop((max(box[0] - TRIM_MARGIN, 0), max(box[1] - TRIM_MARGIN, 0),
                      min(box[2] + TRIM_MARGIN, w), min(box[3] + TRIM_MARGIN, h)))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def _as_jpeg(png: bytes) -> bytes:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


def image_key(video_id: uuid.UUID, index: int, data: bytes) -> str:
    """Named after its content, so a picture drawn again never takes the key of the one it replaces,
    and two runs of a job never share a file one of them might delete."""
    return f"videos/{video_id}/img-{index}-{hashlib.sha256(data).hexdigest()[:12]}.jpg"


@dataclass
class _Drawn:
    index: int
    prompt: str  # the description drawn, as the scene held it
    data: bytes
    served_model: str
    cost: float | None
    key: str | None = None


@dataclass
class _Failed:
    index: int
    reason: str


def _targets(scenes: list[Scene], scene_indexes: Sequence[int] | None) -> list[int]:
    def drawable(s: Scene) -> bool:
        return s.kind == "illustration" and bool(s.image_prompt and s.image_prompt.strip())

    # Each picture is paid for, so the cap of the script holds for an edited one too.
    if sum(drawable(s) for s in scenes) > MAX_ILLUSTRATIONS:
        raise ValueError(f"Video chỉ được có tối đa {MAX_ILLUSTRATIONS} cảnh minh họa")
    if scene_indexes is None:
        return [i for i, s in enumerate(scenes)
                if drawable(s) and (s.image_key is None or s.image_prompt != s.image_prompt_used)]
    for i in scene_indexes:
        if not 0 <= i < len(scenes) or not drawable(scenes[i]):
            raise ValueError(f"Cảnh {i + 1} không phải cảnh minh họa có mô tả hình, nên không vẽ được")
    return sorted(set(scene_indexes))


_DATA_POLICY = re.compile(r"data policy|data_collection|data collection", re.IGNORECASE)


def _refusal_reason(label: str, refusal: ValueError) -> str:
    if _DATA_POLICY.search(str(refusal)):
        # OpenRouter's "No endpoints found matching your data policy": every provider of the model
        # may keep or train on what it is sent, which course material must never allow.
        return (f"không có nhà cung cấp nào của model {label} đáp ứng quy định không thu thập dữ liệu "
                "(data_collection: deny); hãy chọn model ảnh khác")
    return f"model {label} từ chối: {refusal}"


def _draw(alias: str, index: int, scene: Scene) -> _Drawn | _Failed:
    """One scene's picture, or why there is none. Never raises: a scene that fails must not cost
    the other scenes the pictures already paid for."""
    label = IMAGE_MODELS[alias].label if alias in IMAGE_MODELS else alias
    reason = ""
    for _ in range(IMAGE_ATTEMPTS):
        try:
            image = generate_image(alias, image_prompt_for(scene), aspect_ratio=ASPECT_RATIO)
            data = _as_jpeg(trim_whitespace(image.data))
        except MediaError:
            reason = f"model {label} không trả về hình"
            continue
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError):
            # What came back is not a picture Pillow can read; like an empty reply, often a one-off.
            logger.warning(f"[video_images] scene {index}: {label} returned an unreadable image", exc_info=True)
            reason = f"model {label} trả về hình không đọc được"
            continue
        except ValueError as refusal:
            # A refusal (no provider that keeps data_collection deny, a prompt it rejects) repeats
            # on every try; the lecturer needs the model and its words to choose another.
            return _Failed(index, _refusal_reason(label, refusal))
        except Exception:
            logger.exception(f"[video_images] scene {index}: drawing failed unexpectedly")
            return _Failed(index, f"lỗi hệ thống khi vẽ bằng model {label}")
        return _Drawn(index, scene.image_prompt or "", data, image.served_model, image.cost)
    return _Failed(index, reason)


def _failure_message(failures: list[_Failed], scenes: list[Scene]) -> str:
    """Names each scene as the editor numbers it, with its title, and each reason once."""
    by_reason: dict[str, list[str]] = {}
    for f in sorted(failures, key=lambda f: f.index):
        title = scenes[f.index].title.strip()
        if len(title) > _TITLE_CHARS:
            title = title[:_TITLE_CHARS - 1] + "…"
        by_reason.setdefault(f.reason, []).append(f"cảnh {f.index + 1} ({title})")
    parts = [f"{', '.join(names)}: {reason}" for reason, names in by_reason.items()]
    return "Không vẽ được hình cho " + "; ".join(parts) + ". Hãy vẽ lại các cảnh này."


def _store(drawn: list[_Drawn], video_id: uuid.UUID, stored: list[str]) -> None:
    """Appends each key to `stored` as soon as its file is saved, so that a failure partway still
    lets the caller delete the files saved before it."""
    store = media_store()
    for d in drawn:
        d.key = image_key(video_id, d.index, d.data)
        # In the local store's own directory, so that save() is a rename and not a copy across disks.
        pending = staging_dir() / f"pending-{uuid.uuid4()}.jpg"
        pending.write_bytes(d.data)
        try:
            store.save(pending, d.key)
        finally:
            pending.unlink(missing_ok=True)
        stored.append(d.key)


def _delete(keys: list[str]) -> None:
    """Best effort: a file left behind costs storage, while an exception here would hide the error
    being handled, or fail a job whose result is already committed."""
    if not keys:
        return
    try:
        store = media_store()
    except Exception:
        logger.exception(f"[video_images] could not open the media store to delete {keys}")
        return
    for key in keys:
        try:
            store.delete(key)
        except Exception:
            logger.exception(f"[video_images] could not delete {key}")


def draw_illustrations(
    db: Session, video_id: uuid.UUID, scene_indexes: Sequence[int] | None = None,
    *, before_save: Callable[[str | None], None] | None = None,
) -> None:
    """Draws the illustration scenes whose description differs from the one their picture was
    drawn from, or the scenes at `scene_indexes` (0-based), and saves the pictures that were drawn.

    `before_save(error)` runs right before the result is written, in the transaction that writes
    it, with the message the job fails with, or None: the job queue checks there that its worker
    still holds the job, and marks it done only when nothing failed (app/jobs/queue.py, hold).
    Raises ValueError with that message when a scene could not be drawn."""
    with values_kept_across_commits(db):
        video = db.get(Video, video_id)
        if video is None:
            raise ValueError(f"Video {video_id} not found")
        scenes = [Scene.model_validate(s) for s in video.scenes]
        targets = _targets(scenes, scene_indexes)
        alias = video.image_alias
        # No transaction stays open while the model draws.
        db.commit()

        if targets:
            with ThreadPoolExecutor(max_workers=min(PARALLEL_DRAWINGS, len(targets))) as pool:
                results = list(pool.map(lambda i: _draw(alias, i, scenes[i]), targets))
        else:
            results = []
        drawn = [r for r in results if isinstance(r, _Drawn)]
        failures = [r for r in results if isinstance(r, _Failed)]
        error = _failure_message(failures, scenes) if failures else None

        stored: list[str] = []
        try:
            _store(drawn, video_id, stored)
            if before_save is not None:
                before_save(error)
            # Read again, locked: the lecturer may have edited the scenes, or the document may
            # have been deleted, while the model was drawing.
            current = db.get(Video, video_id, with_for_update=True, populate_existing=True)
            if current is None:
                # Nothing is left to show a failure on, so the job ends done even when a scene
                # failed: told of that failure above, it was not marked done, and would otherwise
                # stay running until the sweeper. The job row is locked first, then the video's, the
                # order the queue's error path uses too.
                if error is not None and before_save is not None:
                    before_save(None)
                db.commit()
                _delete(stored)
                return
            now = [Scene.model_validate(s) for s in current.scenes]
            replaced: list[str] = []
            unused: list[str] = []
            for d in drawn:
                scene = now[d.index] if d.index < len(now) else None
                if scene is None or scene.kind != "illustration" or scene.image_prompt != d.prompt:
                    unused.append(d.key)
                    continue
                if scene.image_key and scene.image_key != d.key:
                    replaced.append(scene.image_key)
                scene.image_key, scene.image_prompt_used = d.key, d.prompt
            images = (current.cost or {}).get("images") or {}
            spent = [d.cost for d in drawn if d.cost is not None]
            current.scenes = [s.model_dump() for s in now]
            current.cost = {**(current.cost or {}), "images": {
                "usd": (images.get("usd") or 0.0) + sum(spent),
                "images": (images.get("images") or 0) + len(drawn),
            }}
            current.served_models = [*(current.served_models or []), *(
                {"requested_alias": alias, "routed_alias": alias, "served_model": d.served_model} for d in drawn)]
            current.status = MediaStatus.failed if error else MediaStatus.draft
            current.error = error
            db.commit()
        except BaseException:
            db.rollback()
            _delete(stored)
            raise
        # Only once the new keys are committed: until then the old pictures are what the scenes show.
        _delete(replaced + unused)
    if error:
        raise ValueError(error)


def run_video_images_step(job: Job) -> None:
    payload = job.payload or {}
    video_id = uuid.UUID(payload["video_id"])
    scene_indexes = payload.get("scene_indexes")
    db = WorkerSessionLocal()

    def finish(error: str | None) -> None:
        held = hold(db, job)
        if error is None:
            held.status = JobStatus.done

    try:
        # Marked while the worker holds the job, so a worker that lost it cannot leave the video drawing.
        hold(db, job)
        video = db.get(Video, video_id)
        if video is None:
            raise ValueError(f"Video {video_id} not found")
        video.status = MediaStatus.drawing
        # Cleared so that the error the video shows at the end is this run's (queue.mark_target_failed).
        video.error = None
        db.commit()
        draw_illustrations(db, video_id, scene_indexes, before_save=finish)
    finally:
        db.close()
