"""What a learner reads of a course: the flashcard sets and videos the lecturer published."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_course_reader
from app.api.flashcards import card_sources
from app.db import get_db
from app.models import Course, Document, FlashcardSet, User
from app.models.media import Video

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
            {"id": s.id, "focus": s.focus, "card_count": len(s.cards), "document": _document(d)} for s, d in sets
        ],
        "videos": [
            {"id": v.id, "focus": v.focus, "duration_seconds": v.duration_seconds, "document": _document(d)}
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
    # Only the label of a source: the passage text and the lecturer's review flags are not the learner's.
    cards = [
        {"front": card["front"], "back": card["back"], "sources": [s["locator"] for s in sources]}
        for card, sources in zip(card_set.cards, card_sources(db, card_set), strict=True)
    ]
    return {"id": card_set.id, "course_id": card_set.course_id, "focus": card_set.focus, "cards": cards}
