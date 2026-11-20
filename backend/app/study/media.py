"""Flashcard sets and videos in a study: rated for quality on their own, not compared RAG to no-RAG.

A packet is a snapshot of what the lecturer made, taken when the admin adds it, so later edits do
not change what was rated. Its criteria are its own (docs/rubric.md lists the module ones).
"""
import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.generation.evaluation import DimensionScore, HallucinationFinding
from app.models import Course, Document, FlashcardSet, MediaStatus, StudyItem, Video

MediaKind = Literal["flashcards", "video"]
CRITERIA: dict[str, tuple[str, ...]] = {
    "flashcards": ("accuracy", "focus", "clarity", "no_duplicates"),
    "video": ("accuracy", "focus", "coherence", "visuals_match"),
}
CODE_PREFIX = {"flashcards": "F", "video": "V"}
# Still being written, or never written: nothing to rate yet.
UNRATABLE = (MediaStatus.generating, MediaStatus.failed)


class Card(BaseModel):
    front: str
    back: str


class FlashcardPacket(BaseModel):
    kind: Literal["flashcards"]
    anon_id: str
    topic: str
    cards: list[Card] = Field(min_length=1)


class Scene(BaseModel):
    title: str
    bullets: list[str]
    narration: str


class VideoPacket(BaseModel):
    kind: Literal["video"]
    anon_id: str
    topic: str
    scenes: list[Scene] = Field(min_length=1)
    has_video: bool


class MediaEvaluation(BaseModel):
    anon_id: str
    kind: MediaKind
    scores: dict[str, DimensionScore]
    hallucination_findings: list[HallucinationFinding] = Field(default_factory=list)
    overall_notes: str

    @model_validator(mode="after")
    def every_criterion(self):
        if set(self.scores) != set(CRITERIA[self.kind]):
            raise ValueError(f"scores must be exactly {', '.join(CRITERIA[self.kind])}")
        return self


def packet_model(kind: str):
    return FlashcardPacket if kind == "flashcards" else VideoPacket


def _topic(focus: str | None) -> str:
    return focus or "Toàn bộ tài liệu"


def _owned(db: Session, model, source_id: uuid.UUID, owner_id: uuid.UUID):
    return db.scalar(select(model).join(Course, Course.id == model.course_id)
                     .where(model.id == source_id, Course.owner_id == owner_id))


def media_item(db: Session, kind: MediaKind, source_id: uuid.UUID, owner_id: uuid.UUID) -> StudyItem:
    """The packet of one of the admin's own flashcard sets or videos. LookupError when it is not
    theirs, ValueError when there is nothing to rate yet."""
    source = _owned(db, FlashcardSet if kind == "flashcards" else Video, source_id, owner_id)
    if source is None:
        raise LookupError("Not found")
    if source.status in UNRATABLE:
        raise ValueError("Nothing to rate yet: it is still being made or it failed")
    if kind == "flashcards":
        packet = FlashcardPacket(kind=kind, anon_id="", topic=_topic(source.focus),
                                 cards=[Card(front=c["front"], back=c["back"]) for c in source.cards])
        media_key = None
    else:
        packet = VideoPacket(kind=kind, anon_id="", topic=_topic(source.focus), has_video=source.video_key is not None,
                             scenes=[Scene(title=s["title"], bullets=s.get("bullets", []),
                                           narration=s.get("narration", "")) for s in source.scenes])
        media_key = source.video_key
    return StudyItem(group=kind, source_id=source.id, packet=packet.model_dump(), media_key=media_key,
                     generator_model=source.model_alias)


def media_candidates(db: Session, owner_id: uuid.UUID, in_study: set[uuid.UUID]) -> list[dict]:
    """The admin's flashcard sets and videos with something to rate, newest first."""
    out = []
    for kind, model in (("flashcards", FlashcardSet), ("video", Video)):
        rows = db.execute(select(model, Document.filename, Course.name)
                          .join(Document, Document.id == model.document_id).join(Course, Course.id == model.course_id)
                          .where(Course.owner_id == owner_id, model.status.not_in(UNRATABLE))
                          .order_by(model.created_at.desc())).all()
        out += [{"id": source.id, "kind": kind, "title": f"{filename} · {_topic(source.focus)}",
                 "course_name": course, "created_at": source.created_at,
                 "state": "in_study" if source.id in in_study else "ready"}
                for source, filename, course in rows]
    return out


def next_code(items: list[StudyItem], kind: str) -> str:
    """The next free code of the kind (F01, V01...). Not reblinded: these packets are compared to
    nothing, so their order tells nothing, and codes raters already saw stay as they were."""
    prefix = CODE_PREFIX[kind]
    used = [int(i.anon_code[1:]) for i in items if i.anon_code.startswith(prefix) and i.anon_code[1:].isdigit()]
    return f"{prefix}{max(used, default=0) + 1:02d}"
