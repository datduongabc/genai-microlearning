"""The lecturer's routes for flashcard sets. Learners read published sets through row level security."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_author, require_course_owner
from app.api.model_alias import usable_alias
from app.auth.rate_limit import limiter, user_or_address
from app.citations import citation_label
from app.config import settings
from app.db import get_db
from app.generation import fact_check
from app.generation.common import OutputLanguage
from app.generation.flashcards import MAX_CARDS
from app.generation.limits import reserve_generations
from app.jobs.queue import enqueue_job
from app.llm import catalog
from app.models import Chunk, Course, Document, FlashcardSet, JobType, MediaStatus, User

router = APIRouter(tags=["flashcards"], dependencies=[Depends(get_current_user)])

NO_SOURCE_FLAG = "Chưa có nguồn"


class GenerateRequest(BaseModel):
    focus: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None
    output_language: OutputLanguage = "vi"
    model_alias: str | None = None


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
    require_author(user)
    card_set = db.get(FlashcardSet, set_id, with_for_update=lock)
    if card_set is None:
        raise HTTPException(status_code=404, detail="Flashcard set not found")
    # Both a missing set and someone else's answer 404, so an id does not confirm itself.
    course = db.get(Course, card_set.course_id)
    if course is None or course.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Flashcard set not found")
    return card_set


def _summary(card_set: FlashcardSet) -> dict:
    return {
        "id": card_set.id, "course_id": card_set.course_id, "document_id": card_set.document_id, "focus": card_set.focus,
        "output_language": card_set.output_language, "model_alias": card_set.model_alias,
        "status": card_set.status, "card_count": len(card_set.cards), "published_at": card_set.published_at,
        "created_at": card_set.created_at,
    }


def card_sources(db: Session, card_set: FlashcardSet) -> list[list[dict]]:
    """The passages each card cites, in card order, shared by the lecturer's and the learner's views.

    One query for every card, so a set of 15 cards is not 15 round trips. A chunk that no longer
    exists (the document was re-chunked) simply has no source to show."""
    wanted = {uuid.UUID(c) for card in card_set.cards for c in card["citations"]}
    chunks = {c.id: c for c in db.scalars(select(Chunk).where(Chunk.document_id == card_set.document_id, Chunk.id.in_(wanted)))} if wanted else {}
    filename = db.get(Document, card_set.document_id).filename
    result = []
    for card in card_set.cards:
        sources = []
        for chunk_id in card["citations"]:
            chunk = chunks.get(uuid.UUID(chunk_id))
            if chunk is not None:
                # Stored locators are JSON; the module editor shows the same readable label built from them.
                sources.append({"chunk_id": chunk_id, "locator": citation_label(filename, chunk.source_ref or ""), "content": chunk.content})
        result.append(sources)
    return result


def _detail(db: Session, card_set: FlashcardSet) -> dict:
    cards = [card | {"sources": sources} for card, sources in zip(card_set.cards, card_sources(db, card_set), strict=True)]
    return _summary(card_set) | {"cards": cards}


def _chosen_alias(body: GenerateRequest, user: User) -> str:
    if body.model_alias is not None:
        alias = usable_alias(body.model_alias)
    elif user.default_model_alias is not None and catalog.is_available(user.default_model_alias):
        alias = usable_alias(user.default_model_alias)
    else:
        alias = settings.llm_model
    # The worker fact-checks with a model of another family and fails on an alias that has none;
    # refusing here costs nothing, while failing there is after the reserved generation.
    try:
        fact_check.judge_alias(alias)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"Model '{alias}' không có model kiểm chứng khác họ.") from error
    return alias


@router.post("/flashcard-sets")
@limiter.limit("10/minute", key_func=user_or_address)
def generate_flashcards(
    request: Request, document_id: uuid.UUID, body: GenerateRequest | None = None,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    require_author(current_user)
    body = body or GenerateRequest()
    # Validated before the lock: the probe is a network call and must not hold the document row.
    alias = _chosen_alias(body, current_user)
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

    reserve_generations(db, current_user)
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

    cited = {c for card in body.cards for c in card.citations}
    known = set(db.scalars(select(Chunk.id).where(Chunk.document_id == card_set.document_id, Chunk.id.in_(cited))))
    if cited - known:
        raise HTTPException(status_code=422, detail="Trích nguồn phải là đoạn của chính tài liệu này.")

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
    db.delete(card_set)
    db.commit()
    return {"deleted": True}
