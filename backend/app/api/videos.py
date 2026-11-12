"""The lecturer's routes for videos: generate the script, edit its scenes, draw the pictures, render,
publish, and the slide preview. Learners read published videos through their own routes.

Learners watch the rendered file (`video_key`, `rendered_version`) while the lecturer edits the draft
(`scenes`, `draft_version`): nothing here but a render replaces what they see, and a render only does
when it has finished (app/video/render.py).
"""
import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_author, require_course_owner
from app.api.media_common import NO_SOURCE_FLAG, GenerateRequest, chunk_passages, owned_media, require_own_chunks
from app.api.model_alias import generation_alias
from app.api.stored_files import download_link, send_stored
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.generation.limits import reserve_generations, reserve_video_redraws, reserve_video_render
from app.generation.video_script import (
    MAX_BULLETS,
    MAX_ILLUSTRATIONS,
    MAX_SCENES,
    MAX_SECONDS,
    Scene,
    SceneKind,
    estimated_seconds,
    labels_as_shown,
    prepare_for_render,
    text_words_in_formula,
)
from app.jobs.queue import enqueue_job
from app.llm.media import DEFAULT_IMAGE_MODEL, DEFAULT_VOICE, IMAGE_MODELS, VOICES
from app.models import Course, Document, Job, JobStatus, JobType, MediaStatus, User, Video
from app.storage import delete_media_files, media_keys_for_video, media_store
from app.video.illustrations import scenes_to_draw
from app.video.render import lost_pictures
from app.video.slides import render_slide
from app.video.sources import scene_source_labels

logger = logging.getLogger(__name__)
router = APIRouter(tags=["videos"], dependencies=[Depends(get_current_user)])

NOT_FOUND = "Video not found"
VIDEO_JOBS = (JobType.video_script, JobType.video_images, JobType.video_render)
IN_FLIGHT = (JobStatus.queued, JobStatus.running)
# A slide render starts a Chromium of about 180 MiB. The API task is sized for its requests, not for
# a browser per open editor tab, so at most this many run at once in one process.
PREVIEW_SLOTS = threading.BoundedSemaphore(2)
# Past this a preview answers 503 and the editor asks again, rather than hold the request open.
PREVIEW_WAIT_SECONDS = 1.5
# What a scene's media fields are: the jobs fill them and compare the *_used ones to decide what to
# draw or voice again, so an edit carries them over and never sets them itself.
MEDIA_FIELDS = ("image_key", "image_prompt_used", "voice_key", "voice_text_used", "voice_alias_used")
# A scene with any of these changed is the lecturer's: they decide what the slide shows and says.
CONTENT_FIELDS = ("kind", "title", "bullets", "formula", "code", "table", "image_prompt", "narration")
# How the script job names the items of a scene in its fact check flags (app/generation/video_script.py, _items).
WHOLE_ITEMS = {"formula": "Công thức", "code": "Code", "table": "Bảng"}

# Bounds well above what the script job writes, so a scene the model wrote always saves back.
Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Cell = Annotated[str, StringConstraints(max_length=300)]


class VideoRequest(GenerateRequest):
    voice_alias: str = DEFAULT_VOICE
    image_alias: str = DEFAULT_IMAGE_MODEL


def _distinct(citations: list[list[uuid.UUID]] | None) -> list[list[uuid.UUID]] | None:
    return None if citations is None else [list(dict.fromkeys(item)) for item in citations]


class SceneEdit(BaseModel):
    # The index, in the draft the editor loaded, of the scene this one was: its picture, voice and
    # flags come with it when scenes are moved or removed. Left out, the scene at the same index;
    # null, a new scene with nothing drawn or voiced yet.
    from_index: int | None = None
    kind: SceneKind
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    bullets: Annotated[list[Line], Field(max_length=MAX_BULLETS)] = []
    # bullet_citations[i] are the chunk ids bullets[i] is taken from; left out, none.
    bullet_citations: list[list[uuid.UUID]] | None = None
    formula: Annotated[str, StringConstraints(max_length=2000)] | None = None
    code: Annotated[str, StringConstraints(max_length=4000)] | None = None
    table: Annotated[list[Annotated[list[Cell], Field(max_length=10)]], Field(max_length=20)] | None = None
    image_prompt: Annotated[str, StringConstraints(max_length=2000)] | None = None
    # One sentence per line; blank lines are dropped. narration_citations[i] are line i's sources.
    narration: Annotated[str, StringConstraints(max_length=6000)] = ""
    narration_citations: list[list[uuid.UUID]] | None = None
    # The stored flags the lecturer keeps; left out, all of them. A flag not stored is ignored.
    flags: list[str] | None = None

    _citations_distinct = field_validator("bullet_citations", "narration_citations")(_distinct)


class ScenesRequest(BaseModel):
    # The draft the editor loaded: a save over a newer one (another tab, a second lecturer window)
    # is refused instead of overwriting it.
    draft_version: int
    scenes: Annotated[list[SceneEdit], Field(min_length=1, max_length=MAX_SCENES)]


class IllustrationsRequest(BaseModel):
    # 0-based. Left out, every illustration whose picture is missing or older than its description.
    scene_indexes: list[int] | None = None


# --- Reading ---

def _owned_video(db: Session, video_id: uuid.UUID, user: User, *, lock: bool = False) -> Video:
    return owned_media(db, Video, video_id, user, not_found=NOT_FOUND, lock=lock)


def _job_in_flight(db: Session, video: Video, types=VIDEO_JOBS) -> Job | None:
    """The queued or running job of this video, if any. document_id narrows to the indexed column first."""
    return db.scalars(select(Job).where(
        Job.document_id == video.document_id, Job.job_type.in_(types), Job.status.in_(IN_FLIGHT),
        Job.payload["video_id"].astext == str(video.id),
    ).order_by(Job.created_at.desc()).limit(1)).first()


def _summary(video: Video) -> dict:
    return {
        "id": video.id, "course_id": video.course_id, "document_id": video.document_id, "focus": video.focus,
        "output_language": video.output_language, "model_alias": video.model_alias,
        "voice_alias": video.voice_alias, "image_alias": video.image_alias, "status": video.status,
        "scene_count": len(video.scenes), "draft_version": video.draft_version,
        "rendered_version": video.rendered_version, "duration_seconds": video.duration_seconds,
        "error": video.error, "published_at": video.published_at, "created_at": video.created_at,
    }


def _cited(scene: Scene) -> list[str]:
    """Every chunk the scene cites, in order: the scene's own list, then its bullets and sentences."""
    every = [*scene.citations, *(c for item in scene.bullet_citations for c in item),
             *(c for item in scene.narration_citations for c in item)]
    return list(dict.fromkeys(every))


def _scene_out(scene: Scene, passages: dict[str, dict]) -> dict:
    out = scene.model_dump(exclude=set(MEDIA_FIELDS))
    out["flags"] = _with_source_flags(out["flags"], out)
    drawable = scene.kind == "illustration" and bool(scene.image_prompt and scene.image_prompt.strip())
    return out | {
        "has_image": scene.image_key is not None,
        # A picture drawn from another description than the one shown: "Vẽ lại" brings it up to date.
        "image_outdated": scene.image_key is not None and scene.image_prompt != scene.image_prompt_used,
        "needs_image": drawable and scene.image_key is None,
        "sources": [passages[c] for c in _cited(scene) if c in passages],
    }


def _render_warnings(scenes: list[Scene]) -> list[str]:
    """What the lecturer may want to fix before rendering, without it stopping the render."""
    return [f"Cảnh {n}: hình được vẽ theo mô tả cũ; bấm Vẽ lại nếu cần" for n, s in enumerate(scenes, 1)
            if s.kind == "illustration" and s.image_key is not None and s.image_prompt != s.image_prompt_used]


def _detail(db: Session, video: Video) -> dict:
    scenes = [Scene.model_validate(s) for s in video.scenes]
    passages = chunk_passages(db, video.document_id, (c for s in scenes for c in _cited(s)))
    job = _job_in_flight(db, video)
    return _summary(video) | {
        "scenes": [_scene_out(s, passages) for s in scenes],
        "estimated_seconds": round(estimated_seconds(scenes), 1),
        "max_seconds": MAX_SECONDS,
        "render_problems": prepare_for_render(scenes),
        "render_warnings": _render_warnings(scenes),
        "job": None if job is None else {"id": job.id, "job_type": job.job_type, "status": job.status},
        "cost": video.cost,
    }


# --- Editing ---

def _items(scene: dict) -> dict[str, tuple]:
    """A scene's items by the label its flags use: bullets "Ý n", narration lines "Câu n", and the
    formula, code or table. Each with its sources, so a source added or removed is a change too."""
    def sources(citations: list, n: int) -> list:
        return citations[n] if n < len(citations) else []

    items: dict[str, tuple] = {}
    for n, text in enumerate(scene["bullets"]):
        items[f"Ý {n + 1}"] = (text, sources(scene["bullet_citations"], n))
    for field, label in WHOLE_ITEMS.items():
        if scene.get(field):
            items[label] = (scene[field],)
    lines = scene["narration"].split("\n") if scene["narration"] else []
    for n, text in enumerate(lines):
        items[f"Câu {n + 1}"] = (text, sources(scene["narration_citations"], n))
    return items


_NUMBERED_ITEM = re.compile(r"(Ý|Câu) \d+")


def _flagged_item(flag: str) -> str | None:
    """The item a fact check flag is about, or None for a flag about the whole scene (a script too short)."""
    label = flag.split(":", 1)[0]
    return label if label in WHOLE_ITEMS.values() or _NUMBERED_ITEM.fullmatch(label) else None


def _is_source_flag(flag: str) -> bool:
    return flag.startswith(f"{NO_SOURCE_FLAG}: ")


def _with_source_flags(flags: list[str], scene: dict) -> list[str]:
    """The flags with "Chưa có nguồn" made again from the citations, one per bullet or sentence that
    has none. Not stored state but what the scene is, on every save and every read: no save can lose
    it, the editor cannot dismiss it, and a scene shows it before it was ever saved. The lecturer may
    write without a source (they answer for it), but the editor shows where."""
    kept = [f for f in flags if not _is_source_flag(f)]
    return kept + [f"{NO_SOURCE_FLAG}: {label}" for label, item in _items(scene).items() if len(item) == 2 and not item[1]]


def _new_labels(old: dict[str, tuple], new: dict[str, tuple]) -> dict[str, str]:
    """{old label: new label} for each old item that is still there, found by its text and sources,
    not by its place: removing bullet 1 makes bullet 3 bullet 2, and its flag must follow it. An old
    item with no match was changed or removed."""
    moved: dict[str, str] = {}
    for prefix in ("Ý ", "Câu "):
        left = [label for label in old if label.startswith(prefix)]
        for label in (label for label in new if label.startswith(prefix)):
            match = next((o for o in left if old[o] == new[label]), None)
            if match is not None:
                left.remove(match)
                moved[match] = label
    moved.update({label: label for label in WHOLE_ITEMS.values() if label in old and old[label] == new.get(label)})
    return moved


def _aligned(citations: list[list[uuid.UUID]] | None, items: list[str], number: int, what: str) -> list[list[str]]:
    if citations is None:
        return [[] for _ in items]
    if len(citations) != len(items):
        raise HTTPException(status_code=422, detail=(
            f"Cảnh {number}: có {len(items)} {what} nhưng {len(citations)} danh sách trích nguồn."))
    return [[str(c) for c in item] for item in citations]


def _content(edit: SceneEdit, number: int) -> dict:
    """What the lecturer sent, cleaned as the script job cleans what the model writes: each kind keeps
    only what its slide shows, labels in the image description are written as drawn, and words in a
    formula are set as text. Each step leaves an already clean value as it is, so an unchanged scene
    compares equal to the stored one."""
    lines = [line.strip() for line in edit.narration.split("\n") if line.strip()]
    kind = edit.kind
    formula = edit.formula.strip() if kind == "formula" and edit.formula and edit.formula.strip() else None
    code = edit.code if kind == "code" and edit.code and edit.code.strip() else None
    prompt = edit.image_prompt if kind == "illustration" and edit.image_prompt and edit.image_prompt.strip() else None
    return {
        "kind": kind, "title": edit.title, "bullets": list(edit.bullets),
        "bullet_citations": _aligned(edit.bullet_citations, edit.bullets, number, "ý"),
        "formula": text_words_in_formula(formula) if formula else None,
        "code": code,
        "table": edit.table if kind == "table" and edit.table else None,
        "image_prompt": labels_as_shown(prompt) if prompt else None,
        "narration": "\n".join(lines),
        "narration_citations": _aligned(edit.narration_citations, lines, number, "câu thoại"),
    }


def _sources(edit: SceneEdit) -> list[str]:
    """The scene's own citation list: its bullets' and sentences' sources, in order."""
    every = [*(c for item in edit.bullet_citations or [] for c in item),
             *(c for item in edit.narration_citations or [] for c in item)]
    return [str(c) for c in dict.fromkeys(every)]


def _edited_scene(edit: SceneEdit, before: dict | None, number: int) -> dict:
    before = Scene.model_validate(before).model_dump() if before is not None else None
    now = _content(edit, number)
    changed = before is None or any(before[f] != now[f] for f in CONTENT_FIELDS)
    new_items = _items(now)
    moved = _new_labels(_items(before), new_items) if before is not None else {}

    stored = [f for f in (before["flags"] if before is not None else []) if not _is_source_flag(f)]
    if edit.flags is not None:
        stored = [f for f in stored if f in set(edit.flags)]
    flags = []
    for flag in stored:
        label = _flagged_item(flag)
        if label is None:
            flags.append(flag)
        elif label in moved:
            # A flag about an item that is still there follows it to its new number.
            flags.append(moved[label] + flag[len(label):])
        # A flag about an item that was changed or removed was about the old item, so it goes.
    flags = _with_source_flags(flags, now)

    media = {f: before[f] for f in MEDIA_FIELDS} if before is not None else {}
    return Scene(
        **now, citations=_sources(edit), author="lecturer" if changed else before["author"], flags=flags, **media,
    ).model_dump()


def _origins(body: ScenesRequest, stored: list) -> list[int | None]:
    origins = []
    for i, edit in enumerate(body.scenes):
        origin = edit.from_index if "from_index" in edit.model_fields_set else (i if i < len(stored) else None)
        if origin is not None and not 0 <= origin < len(stored):
            raise HTTPException(status_code=422, detail=f"Cảnh {i + 1}: không có cảnh {origin + 1} trong bản nháp.")
        origins.append(origin)
    taken = [o for o in origins if o is not None]
    if len(taken) != len(set(taken)):
        # Two scenes sharing one picture: drawing one again would delete the other's.
        raise HTTPException(status_code=422, detail="Một cảnh của bản nháp chỉ được dùng cho một cảnh.")
    return origins


@router.put("/videos/{video_id}/scenes")
def edit_scenes(
    video_id: uuid.UUID, body: ScenesRequest, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Saves the draft. Allowed while pictures are drawn or a render runs: each job checks, when it
    saves, that the scene still says what it drew or voiced, and a render renders the draft it read."""
    video = _owned_video(db, video_id, current_user, lock=True)
    if video.status == MediaStatus.generating:
        raise HTTPException(status_code=409, detail="Lời thoại đang được sinh, vui lòng chờ xong rồi sửa.")
    if body.draft_version != video.draft_version:
        raise HTTPException(status_code=409, detail="Bản nháp đã được lưu ở nơi khác. Hãy tải lại trang rồi sửa tiếp.")

    stored = list(video.scenes)
    origins = _origins(body, stored)
    require_own_chunks(db, video.document_id, {
        c for edit in body.scenes for item in (edit.bullet_citations or []) + (edit.narration_citations or [])
        for c in item})
    scenes = [_edited_scene(edit, stored[origin] if origin is not None else None, number)
              for number, (edit, origin) in enumerate(zip(body.scenes, origins, strict=True), 1)]
    if sum(s["kind"] == "illustration" and s["image_prompt"] is not None for s in scenes) > MAX_ILLUSTRATIONS:
        raise HTTPException(status_code=422, detail=f"Video chỉ được có tối đa {MAX_ILLUSTRATIONS} cảnh minh họa.")

    # The files of a scene that is gone are no one's. A job that may still read them (it read the
    # scenes before this edit) keeps them; they are then left behind, which costs only storage.
    kept = {s[f] for s in scenes for f in ("image_key", "voice_key") if s.get(f)}
    dropped = [] if _job_in_flight(db, video, (JobType.video_images, JobType.video_render)) else [
        stored[i][f] for i in set(range(len(stored))) - {o for o in origins if o is not None}
        for f in ("image_key", "voice_key") if stored[i].get(f) and stored[i][f] not in kept]

    video.scenes = scenes
    video.draft_version += 1
    db.commit()
    delete_media_files(dropped)
    db.refresh(video)
    return _detail(db, video)


# --- Generating, drawing, rendering ---

@router.post("/videos")
@limiter.limit("10/minute", key_func=user_or_address)
def generate_video(
    request: Request, document_id: uuid.UUID, body: VideoRequest | None = None,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    require_author(current_user)
    body = body or VideoRequest()
    # Validated before the lock: the probe is a network call and must not hold the document row.
    alias = generation_alias(body.model_alias, current_user)
    if body.voice_alias not in VOICES:
        raise HTTPException(status_code=422, detail=f"Không có giọng đọc nào tên '{body.voice_alias}'.")
    if body.image_alias not in IMAGE_MODELS:
        raise HTTPException(status_code=422, detail=f"Không có model vẽ hình nào tên '{body.image_alias}'.")
    focus = body.focus or None
    # FOR UPDATE: the duplicate check below reads the videos table, so two identical requests for one
    # document must not run it at the same moment or both would pass.
    doc = db.get(Document, document_id, with_for_update=True)
    if doc is None or doc.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.raw_text:
        raise HTTPException(status_code=422, detail="Document chưa được parse (raw_text rỗng)")

    # Only an identical request is a double submit: a lecturer may want several videos of one document.
    duplicate = db.query(Video.id).filter(
        Video.document_id == doc.id, Video.status == MediaStatus.generating,
        Video.output_language == body.output_language, Video.focus.is_not_distinct_from(focus),
    ).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Video này đang được sinh lời thoại, vui lòng chờ.")

    reserve_generations(db, current_user)
    video = Video(
        course_id=doc.course_id, document_id=doc.id, focus=focus, output_language=body.output_language,
        model_alias=alias, voice_alias=body.voice_alias, image_alias=body.image_alias, status=MediaStatus.generating,
    )
    db.add(video)
    db.flush()
    job = enqueue_job(db, job_type=JobType.video_script, step="script", document_id=doc.id,
                      payload={"video_id": str(video.id)})
    db.commit()
    return {"id": video.id, "job_id": job.id}


def _refuse_while_busy(db: Session, video: Video) -> None:
    # The video row is locked by the caller, so two clicks cannot both pass this.
    job = _job_in_flight(db, video)
    if job is not None:
        what = {JobType.video_script: "Lời thoại đang được sinh", JobType.video_images: "Hình đang được vẽ",
                JobType.video_render: "Video đang được dựng"}[job.job_type]
        raise HTTPException(status_code=409, detail=f"{what}, vui lòng chờ xong.")


@router.post("/videos/{video_id}/illustrations")
def queue_illustrations(
    video_id: uuid.UUID, body: IllustrationsRequest | None = None, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Queues the drawing of the scenes asked for. Every picture counts against the day's limit,
    and the job is given the scenes counted, so what is drawn is what was counted."""
    video = _owned_video(db, video_id, current_user, lock=True)
    _refuse_while_busy(db, video)
    scenes = [Scene.model_validate(s) for s in video.scenes]
    try:
        targets = scenes_to_draw(scenes, (body.scene_indexes if body else None))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not targets:
        raise HTTPException(status_code=422, detail="Không có cảnh minh họa nào cần vẽ: mọi hình đã khớp với mô tả.")

    reserve_video_redraws(db, current_user, len(targets))
    job = enqueue_job(db, job_type=JobType.video_images, step="images", document_id=video.document_id,
                      payload={"video_id": str(video.id), "scene_indexes": targets})
    db.commit()
    return {"job_id": job.id, "scene_indexes": targets}


@router.post("/videos/{video_id}/render")
def queue_render(video_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Queues a render of the draft as it is. What would fail the render at its start (too long, a
    picture missing) is refused here, before anything is counted."""
    video = _owned_video(db, video_id, current_user, lock=True)
    _refuse_while_busy(db, video)
    scenes = [Scene.model_validate(s) for s in video.scenes]
    problems = prepare_for_render(scenes) + lost_pictures(scenes)
    if problems:
        raise HTTPException(status_code=422, detail="Chưa dựng được video: " + "; ".join(problems) + ".")

    reserve_video_render(db, current_user)
    job = enqueue_job(db, job_type=JobType.video_render, step="render", document_id=video.document_id,
                      payload={"video_id": str(video.id)})
    db.commit()
    return {"job_id": job.id}


# --- Listing, publishing, deleting ---

@router.get("/courses/{course_id}/videos")
def list_videos(course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_author(current_user)
    require_course_owner(db.get(Course, course_id), current_user)
    videos = db.scalars(select(Video).where(Video.course_id == course_id).order_by(Video.created_at.desc())).all()
    return [_summary(v) for v in videos]


@router.get("/videos/{video_id}")
def get_video(video_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _detail(db, _owned_video(db, video_id, current_user))


@router.post("/videos/{video_id}/publish")
def publish_video(video_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    video = _owned_video(db, video_id, current_user, lock=True)
    if video.rendered_version is None or video.video_key is None:
        raise HTTPException(status_code=409, detail="Chỉ giao được video đã dựng xong.")
    if video.published_at is None:
        video.published_at = func.now()
    db.commit()
    db.refresh(video)
    return _detail(db, video)


@router.post("/videos/{video_id}/unpublish")
def unpublish_video(video_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    video = _owned_video(db, video_id, current_user, lock=True)
    video.published_at = None
    db.commit()
    db.refresh(video)
    return _detail(db, video)


@router.delete("/videos/{video_id}")
def delete_video(video_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    video = _owned_video(db, video_id, current_user, lock=True)
    # A job of this video would fail on the missing row, or store files no row names any more.
    _refuse_while_busy(db, video)
    keys = media_keys_for_video(db, video.id)
    db.delete(video)
    db.commit()
    # Committed first: a file without its row is harmless, a row whose file is gone is not.
    delete_media_files(keys)
    return {"deleted": True}


# --- Files ---

def _rendered(db: Session, video_id: uuid.UUID, user: User) -> tuple[Video, str]:
    video = _owned_video(db, video_id, user)
    if video.video_key is None:
        raise HTTPException(status_code=404, detail="Video chưa được dựng.")
    filename = db.scalar(select(Document.filename).where(Document.id == video.document_id)) or "video"
    return video, f"{Path(filename).stem or 'video'}.mp4"


@router.get("/videos/{video_id}/file")
def video_file(video_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Where the lecturer's browser gets the rendered MP4: a link valid for a few minutes on S3, or an
    API path to fetch with the token without it. The page asks again for each play."""
    video, filename = _rendered(db, video_id, current_user)
    return download_link(media_store(), video.video_key, filename, f"/videos/{video.id}/file/content")


@router.get("/videos/{video_id}/file/content")
def video_file_content(video_id: uuid.UUID, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    video, filename = _rendered(db, video_id, current_user)
    return send_stored(media_store(), video.video_key, filename)


def _stored_image(scene: Scene) -> bytes | None:
    """The scene's picture as the render would show it, or none when it has none or its file is gone:
    the preview then shows the text slide, and the detail's render_problems says what is missing."""
    if scene.kind != "illustration" or not scene.image_key:
        return None
    try:
        with media_store().local_copy(scene.image_key) as path:
            return Path(path).read_bytes()
    except Exception:
        # A missing file on disk or on S3 alike: a preview is for looking, and must not fail on it.
        logger.warning(f"[videos] preview could not read {scene.image_key}", exc_info=True)
        return None


@router.get("/videos/{video_id}/scenes/{index}/preview.png")
def scene_preview(video_id: uuid.UUID, index: int, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """The slide of the draft's scene `index` (0-based) as the render would print it, with the
    picture it has now. Not cached: the draft changes under the same URL."""
    video = _owned_video(db, video_id, current_user)
    if not 0 <= index < len(video.scenes):
        raise HTTPException(status_code=404, detail="Scene not found")
    scene = Scene.model_validate(video.scenes[index])
    label = scene_source_labels(db, video, [scene])[0]
    lang = video.output_language
    # No transaction stays open while the picture is read and the browser renders.
    db.commit()
    image = _stored_image(scene)
    if not PREVIEW_SLOTS.acquire(timeout=PREVIEW_WAIT_SECONDS):
        raise HTTPException(status_code=503, detail="Máy chủ đang bận dựng slide khác, vui lòng thử lại sau giây lát.",
                            headers={"Retry-After": "2"})
    try:
        # A sync route already runs in the threadpool; render_slide adds a thread for Playwright.
        png = render_slide(scene, image, label, lang=lang)
    finally:
        PREVIEW_SLOTS.release()
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, no-store"})
