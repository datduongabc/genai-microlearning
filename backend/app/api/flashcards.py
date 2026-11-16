"""The lecturer's routes for flashcard sets. Learners read published sets through row level security."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_author, require_course_owner
from app.api.media_common import NO_SOURCE_FLAG, GenerateRequest, chunk_passages, owned_media, require_own_chunks
from app.api.model_alias import generation_alias
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.generation.flashcards import MAX_CARDS
from app.generation.limits import has_full_access, reserve_generations
from app.jobs.queue import enqueue_job
from app.models import Course, Document, FlashcardSet, Job, JobType, MediaStatus, User

router = APIRouter(tags=["flashcards"], dependencies=[Depends(get_current_user)])

class CardEdit(BaseModel):
    front: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    back: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    citations: list[uuid.UUID] = []

    @field_validator("citations")
    @classmethod
    def _distinct(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))


class CardsRequest(BaseModel):
    cards: Annotated[list[CardEdit], Field(max_length=MAX_CARDS)]


def _owned_set(db: Session, set_id: uuid.UUID, user: User, *, lock: bool = False) -> FlashcardSet:
    return owned_media(db, FlashcardSet, set_id, user, not_found="Flashcard set not found", lock=lock)


def _summary(card_set: FlashcardSet) -> dict:
    return {
        "id": card_set.id, "course_id": card_set.course_id, "document_id": card_set.document_id, "focus": card_set.focus,
        "output_language": card_set.output_language, "model_alias": card_set.model_alias,
        "status": card_set.status, "card_count": len(card_set.cards), "published_at": card_set.published_at,
        "created_at": card_set.created_at, "cost": card_set.cost,
    }


def card_sources(db: Session, card_set: FlashcardSet) -> list[list[dict]]:
    """The passages each card cites, in card order, shared by the lecturer's and the learner's views."""
    passages = chunk_passages(db, card_set.document_id, (c for card in card_set.cards for c in card["citations"]))
    return [[passages[c] for c in card["citations"] if c in passages] for card in card_set.cards]


def _detail(db: Session, card_set: FlashcardSet) -> dict:
    cards = [card | {"sources": sources} for card, sources in zip(card_set.cards, card_sources(db, card_set), strict=True)]
    return _summary(card_set) | {"cards": cards}


@router.post("/flashcard-sets")
@limiter.limit("10/minute", key_func=user_or_address)
def generate_flashcards(
    request: Request, document_id: uuid.UUID, body: GenerateRequest | None = None,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    require_author(current_user)
    body = body or GenerateRequest()
    # Before the lock: the model probe and the Moodle check are network calls and must not hold a row.
    alias = generation_alias(body.model_alias, current_user)
    full_access = has_full_access(db, current_user)
    focus = body.focus or None
    # FOR UPDATE: the duplicate check below reads the sets table, so two identical requests for one
    # document must not run it at the same moment or both would pass.
    doc = db.get(Document, document_id, with_for_update=True)
    if doc is None or doc.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.raw_text:
        raise HTTPException(status_code=422, detail="Document chưa được parse (raw_text rỗng)")

    # Only an identical request is a double submit: a lecturer may want several sets of one document
    # (arrays, then linked lists), each with its own focus.
    duplicate = db.query(FlashcardSet.id).filter(
        FlashcardSet.document_id == doc.id, FlashcardSet.status == MediaStatus.generating,
        FlashcardSet.output_language == body.output_language,
        FlashcardSet.focus.is_not_distinct_from(focus),
    ).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Bộ thẻ này đang được sinh, vui lòng chờ.")

    reserve_generations(db, current_user, full_access=full_access, noun="bộ flashcard")
    card_set = FlashcardSet(
        course_id=doc.course_id, document_id=doc.id, focus=focus,
        output_language=body.output_language, model_alias=alias, status=MediaStatus.generating,
    )
    db.add(card_set)
    db.flush()
    job = enqueue_job(
        db, job_type=JobType.flashcard_generation, step="generate", document_id=doc.id,
        payload={"flashcard_set_id": str(card_set.id)},
    )
    db.commit()
    return {"id": card_set.id, "job_id": job.id}


@router.get("/courses/{course_id}/flashcard-sets")
def list_flashcard_sets(
    course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    require_author(current_user)
    require_course_owner(db.get(Course, course_id), current_user)
    sets = db.scalars(
        select(FlashcardSet).where(FlashcardSet.course_id == course_id).order_by(FlashcardSet.created_at.desc())
    ).all()
    return [_summary(s) for s in sets]


@router.get("/flashcard-sets/{set_id}")
def get_flashcard_set(set_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _detail(db, _owned_set(db, set_id, current_user))


@router.put("/flashcard-sets/{set_id}/cards")
def edit_cards(
    set_id: uuid.UUID, body: CardsRequest, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    card_set = _owned_set(db, set_id, current_user, lock=True)
    if card_set.status != MediaStatus.draft:
        raise HTTPException(status_code=409, detail="Chỉ sửa được thẻ của bộ thẻ đã sinh xong.")
    if card_set.published_at is not None and not body.cards:
        raise HTTPException(status_code=422, detail="Bộ thẻ đang công khai không thể để trống, hãy ẩn nó trước.")

    require_own_chunks(db, card_set.document_id, {c for card in body.cards for c in card.citations})

    stored = card_set.cards
    edited = []
    for index, card in enumerate(body.cards):
        citations = [str(c) for c in card.citations]
        before = stored[index] if index < len(stored) else None
        if before is not None and (before["front"], before["back"], before["citations"]) == (
                card.front, card.back, citations):
            edited.append(before)
            continue
        edited.append({
            "front": card.front, "back": card.back, "citations": citations, "author": "lecturer",
            "flag": None if citations else NO_SOURCE_FLAG,
        })
    card_set.cards = edited
    db.commit()
    return _detail(db, card_set)


@router.post("/flashcard-sets/{set_id}/publish")
def publish_flashcard_set(
    set_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    card_set = _owned_set(db, set_id, current_user, lock=True)
    if card_set.status != MediaStatus.draft or not card_set.cards:
        raise HTTPException(status_code=409, detail="Chỉ công khai được bộ thẻ đã sinh xong và có ít nhất một thẻ.")
    if card_set.published_at is None:
        card_set.published_at = func.now()
    db.commit()
    db.refresh(card_set)
    return _detail(db, card_set)


@router.post("/flashcard-sets/{set_id}/unpublish")
def unpublish_flashcard_set(
    set_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    card_set = _owned_set(db, set_id, current_user, lock=True)
    card_set.published_at = None
    db.commit()
    return _detail(db, card_set)


@router.delete("/flashcard-sets/{set_id}")
def delete_flashcard_set(
    set_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    card_set = _owned_set(db, set_id, current_user, lock=True)
    # The worker still holds this set; deleting it now would fail its job on a missing row.
    if card_set.status == MediaStatus.generating:
        raise HTTPException(status_code=409, detail="Bộ thẻ đang được sinh, vui lòng chờ xong rồi xóa.")
    # A flashcard set owns no files, so unlike a module or a video there is nothing to remove from storage.
    # Its jobs have no row to cascade from; a failed one kept would list the document as failed,
    # with a retry that can only fail on the missing set.
    db.execute(delete(Job).where(Job.document_id == card_set.document_id,
                                 Job.payload["flashcard_set_id"].astext == str(card_set.id)))
    db.delete(card_set)
    db.commit()
    return {"deleted": True}
