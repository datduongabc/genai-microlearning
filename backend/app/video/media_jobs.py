"""What the jobs that pay for a video's media share: the illustration job (app/video/illustrations.py)
and the render job (app/video/render.py). Both call a model once per scene, keep what a scene got
when another scene fails, name the failed scenes to the lecturer, and store and delete files in
the media store."""

import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.generation.video_script import Scene
from app.models import Video
from app.storage import media_store, staging_dir

logger = logging.getLogger(__name__)

_TITLE_CHARS = 60
_DATA_POLICY = re.compile(r"data policy|data_collection|data collection", re.IGNORECASE)


@dataclass
class SceneFailure:
    index: int
    reason: str


def refusal_reason(label: str, refusal: ValueError, kind: str) -> str:
    """Why model `label` refused, in the lecturer's words. `kind` names the kind of model ("ảnh",
    "giọng") the lecturer should choose another of."""
    if _DATA_POLICY.search(str(refusal)):
        # OpenRouter's "No endpoints found matching your data policy": every provider of the model
        # may keep or train on what it is sent, which course material must never allow.
        return (f"mô hình {label} không có nhà cung cấp nào cam kết không lưu dữ liệu, "
                f"hãy chọn mô hình {kind} khác")
    return f"mô hình {label} từ chối: {refusal}"


def scene_failure_message(failures: Sequence[SceneFailure], scenes: Sequence[Scene], lead: str, tail: str) -> str:
    """Names each scene as the editor numbers it, with its title, and each reason once:
    "<lead>cảnh 2 (FCFS), cảnh 4 (SJF): <reason>. <tail>"."""
    by_reason: dict[str, list[str]] = {}
    for f in sorted(failures, key=lambda f: f.index):
        title = scenes[f.index].title.strip()
        if len(title) > _TITLE_CHARS:
            title = title[:_TITLE_CHARS - 1] + "…"
        by_reason.setdefault(f.reason, []).append(f"cảnh {f.index + 1} ({title})")
    parts = [f"{', '.join(names)}: {reason}" for reason, names in by_reason.items()]
    return lead + "; ".join(parts) + f". {tail}"


def store_bytes(data: bytes, key: str, stored: list[str]) -> None:
    """Saves `data` under `key`, then appends the key to `stored`, so that a failure partway still
    lets the caller delete the files saved before it."""
    # In the local store's own directory, so that save() is a rename and not a copy across disks.
    pending = staging_dir() / f"pending-{uuid.uuid4()}"
    pending.write_bytes(data)
    store_file(pending, key, stored)


def store_file(path: Path, key: str, stored: list[str]) -> None:
    """Moves the file at `path` into the store under `key`; `path` is gone afterwards."""
    try:
        media_store().save(path, key)
    finally:
        path.unlink(missing_ok=True)
    stored.append(key)


def delete_files(keys: Sequence[str], tag: str) -> None:
    """Best effort: a file left behind costs storage, while an exception here would hide the error
    being handled, or fail a job whose result is already committed."""
    if not keys:
        return
    try:
        store = media_store()
    except Exception:
        logger.exception(f"[{tag}] could not open the media store to delete {list(keys)}")
        return
    for key in keys:
        try:
            store.delete(key)
        except Exception:
            logger.exception(f"[{tag}] could not delete {key}")


def scene_file_keys(scenes: Sequence[dict]) -> set[str]:
    """The picture and voice files the scenes name."""
    return {s[f] for s in scenes for f in ("image_key", "voice_key") if s.get(f)}


def delete_unnamed_files(db: Session, video_id: uuid.UUID, keys_at_start: set[str], tag: str) -> None:
    """Run by a media job after its last write, whether it succeeded or failed: deletes the files its
    scenes named when it started that the video no longer names. A scene the lecturer removed while
    the job ran kept its files (the job might still read them), and nothing else would delete them.

    Best effort, like delete_files: the job's result is committed already."""
    try:
        # A failed job leaves its transaction aborted; what is read here must be the committed state.
        db.rollback()
        video = db.get(Video, video_id, populate_existing=True)
        named = set() if video is None else scene_file_keys(video.scenes) | {video.video_key}
        db.commit()
    except Exception:
        logger.exception(f"[{tag}] could not read video {video_id} to clean up its files")
        return
    delete_files(sorted(keys_at_start - named), tag)
