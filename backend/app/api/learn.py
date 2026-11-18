"""What a learner reads of a course: the flashcard sets and videos the lecturer published."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_course_reader
from app.api.flashcards import card_sources
from app.api.stored_files import send_stored
from app.api.videos import rendered_filename, rendered_link
from app.citations import citation_label
from app.db import get_db
from app.models import Chunk, Course, Document, FlashcardSet, User
from app.models.media import Video
from app.storage import media_store
from app.video.render import rendered_captions

router = APIRouter(prefix="/learn", tags=["learn"], dependencies=[Depends(get_current_user)])


def _document(document: Document) -> dict:
    return {"id": document.id, "filename": document.filename}


@router.get("/courses/{course_id}/items")
def list_items(course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_course_reader(db, db.get(Course, course_id), current_user)
    # Filtered here as well as by row level security: the owner previewing the page passes the policy for
    # every row, drafts included, and must see what a learner sees.
    sets = db.execute(
        select(FlashcardSet, Document).join(Document, Document.id == FlashcardSet.document_id)
        .where(FlashcardSet.course_id == course_id, FlashcardSet.published_at.is_not(None))
        .order_by(Document.filename, FlashcardSet.created_at)
    ).all()
    videos = db.execute(
        select(Video, Document).join(Document, Document.id == Video.document_id)
        .where(Video.course_id == course_id, Video.published_at.is_not(None))
        .order_by(Document.filename, Video.created_at)
    ).all()
    return {
        "flashcard_sets": [
            {"id": s.id, "focus": s.focus, "card_count": len(s.cards), "published_at": s.published_at,
             "document": _document(d)} for s, d in sets
        ],
        "videos": [
            {"id": v.id, "focus": v.focus, "duration_seconds": v.duration_seconds,
             "published_at": v.published_at, "document": _document(d)}
            for v, d in videos
        ],
    }


@router.get("/flashcard-sets/{set_id}")
def get_set(set_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    card_set = db.get(FlashcardSet, set_id)
    # An unpublished set answers like a missing one, also to the owner, who sees every row through the policy.
    if card_set is None or card_set.published_at is None:
        raise HTTPException(status_code=404, detail="Flashcard set not found")
    try:
        require_course_reader(db, db.get(Course, card_set.course_id), current_user)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Flashcard set not found") from None
    # Only the label of a source, once even if several cited passages share it: the passage text and the lecturer's review flags are not the learner's.
    cards = [
        {"front": card["front"], "back": card["back"], "sources": list(dict.fromkeys(s["locator"] for s in sources))}
        for card, sources in zip(card_set.cards, card_sources(db, card_set), strict=True)
    ]
    return {"id": card_set.id, "course_id": card_set.course_id, "focus": card_set.focus, "cards": cards}


def _watchable_video(db: Session, video_id: uuid.UUID, user: User) -> Video:
    """A published video with a rendered file, for a reader of its course. Every other case answers the
    same 404, so nothing tells a hidden video from one that does not exist, and no link goes with it."""
    not_found = HTTPException(status_code=404, detail="Video not found")
    video = db.get(Video, video_id)
    if video is None or video.published_at is None or video.video_key is None:
        raise not_found
    try:
        require_course_reader(db, db.get(Course, video.course_id), user)
    except HTTPException:
        raise not_found from None
    return video


def _chunk_places(db: Session, document_id: uuid.UUID, chunk_ids: set[str]) -> dict[str, str]:
    """{chunk id: where it is in the document ("trang 9 · FCFS")}, for the cited chunks that still exist.
    Without the file name: every source of a video is in its one document, named once beside the list."""
    wanted = set()
    for chunk_id in chunk_ids:
        try:
            wanted.add(uuid.UUID(chunk_id))
        except ValueError:
            continue
    if not wanted:
        return {}
    rows = db.execute(select(Chunk.id, Chunk.source_ref).where(Chunk.document_id == document_id, Chunk.id.in_(wanted)))
    return {str(chunk_id): citation_label("", source_ref or "") for chunk_id, source_ref in rows}


@router.get("/videos/{video_id}")
def get_video(video_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    video = _watchable_video(db, video_id, current_user)
    # The scenes of the render, not the draft: the draft can be newer than the file being watched.
    rendered = video.rendered_scenes or []
    places = _chunk_places(db, video.document_id, {c for scene in rendered for c in scene["citations"]})
    scenes = [
        {
            "title": scene["title"], "start_seconds": scene["start_seconds"],
            # Where in the document, once even if several cited passages share it. The file is named
            # once for the video: on every line it took a scene of five sources 290 px at 390 wide.
            "sources": list(dict.fromkeys(places[c] for c in scene["citations"] if places.get(c))),
        }
        for scene in rendered
    ]
    link = rendered_link(db, video, f"/learn/videos/{video.id}/file/content")
    return {
        "id": video.id, "course_id": video.course_id, "title": video.focus,
        "duration_seconds": video.duration_seconds,
        # The render these scenes and this file belong to: a page that asks for a new link can tell
        # from it that the lecturer rendered again meanwhile.
        "rendered_version": video.rendered_version,
        "file_url": link["url"], "via_api": link["via_api"],
        "source_document": db.scalar(select(Document.filename).where(Document.id == video.document_id)),
        "scenes": scenes,
        "language": video.output_language,
        "captions": rendered_captions(video),
    }


@router.get("/videos/{video_id}/file/content")
def get_video_file(video_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    video = _watchable_video(db, video_id, current_user)
    return send_stored(media_store(), video.video_key, rendered_filename(db, video))
