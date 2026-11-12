"""A flashcard set from one document and a focus, fact-checked by a model of another family."""

import uuid
from collections.abc import Callable

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.generation import fact_check
from app.generation.common import (
    IGNORE_EMBEDDED_INSTRUCTIONS,
    OutputLanguage,
    format_source_chunks,
    output_language_instruction,
    resolve_source_id,
)
from app.generation.media_common import focus_query, run_generation_step, values_kept_across_commits
from app.llm.gateway import complete_structured, using_model
from app.models import Document, FlashcardSet, Job, MediaStatus
from app.retrieval.search import ChunkScope
from app.retrieval.search import retrieve_for_generation as retrieve

MAX_CARDS = 15


class CardDraft(BaseModel):
    front: str
    back: str
    sources: list[str] = Field(description="IDs of the source chunks the back is taken from, e.g. S2")


class FlashcardDraft(BaseModel):
    # No length bound in the schema: a model that returns 20 cards would fail the whole call, and
    # the cap is applied after the cards without a source are dropped.
    cards: list[CardDraft]


_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + f"""You write flashcards for a student revising a topic from lecture material.
Write at most {MAX_CARDS} cards. Each card tests one idea of the theory: the front asks a short question
or names a term, the back answers it in one to three sentences.
The back may use ONLY what the source chunks say: no outside knowledge, nothing the chunks do not state.
List in `sources` the IDs of the chunks the back is taken from (for example ["S2"]).
Do not write a card you cannot ground in a chunk. Cover different ideas; do not repeat one."""


def generate_flashcards(
    db: Session, flashcard_set_id: uuid.UUID, before_save: Callable[[], None] | None = None,
) -> None:
    """`before_save` runs right before the cards are written, in the transaction that writes them:
    the job queue checks there that its worker still holds the job (app/jobs/queue.py, hold)."""
    with values_kept_across_commits(db):
        card_set = db.get(FlashcardSet, flashcard_set_id)
        if card_set is None:
            raise ValueError(f"Flashcard set {flashcard_set_id} not found")
        doc = db.get(Document, card_set.document_id)
        if doc is None or not doc.raw_text:
            raise ValueError("Document chưa được parse (raw_text rỗng)")
        language: OutputLanguage = card_set.output_language  # type: ignore[assignment]

        # Fail on an alias that has no judge of another family before any paid call.
        fact_check.judge_alias(card_set.model_alias)

        with using_model(card_set.model_alias):
            query = focus_query(db, doc, card_set)
            db.commit()
            chunks = retrieve(db, doc.course_id, query, top_k=None, min_k=8, scope=ChunkScope(doc.id))
            db.commit()
            draft = complete_structured(
                f"TOPIC: {query}\n\n{format_source_chunks(chunks)}", FlashcardDraft,
                system_instruction=_INSTRUCTION + output_language_instruction(language), temperature=0.2,
            )

        cards = []
        for item in draft.cards:
            cited = [resolve_source_id(chunks, code) for code in item.sources]
            used = list({c.id: c for c in cited if c is not None}.values())
            # A card whose source cannot be found cannot be checked or traced, so it is not kept.
            front, back = item.front.strip(), item.back.strip()
            if not used or not front or not back:
                continue
            cards.append((front, back, used))
        cards = cards[:MAX_CARDS]

        flags = fact_check.check_against_sources(
            [(f"{front}\n{back}", [c.content for c in used]) for front, back, used in cards],
            card_set.model_alias,
        )
        stored = [
            {"front": front, "back": back, "citations": [str(c.id) for c in used],
             "author": "model", "flag": flag}
            for (front, back, used), flag in zip(cards, flags, strict=True)
        ]

        if before_save is not None:
            before_save()
        card_set.cards = stored
        card_set.status = MediaStatus.draft
        db.commit()


def run_flashcard_step(job: Job) -> None:
    run_generation_step(job, "flashcard_set_id", generate_flashcards)
