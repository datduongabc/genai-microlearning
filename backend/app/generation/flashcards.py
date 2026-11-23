"""A flashcard set from one document and a focus, fact-checked by a model of another family."""

import time
import uuid
from collections.abc import Callable

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import timing
from app.generation import fact_check
from app.generation.common import (
    IGNORE_EMBEDDED_INSTRUCTIONS,
    OutputLanguage,
    format_source_chunks,
    output_language_instruction,
    resolve_source_id,
)
from app.generation.media_common import (
    focus_passages,
    llm_cost,
    run_generation_step,
    term_rules,
    topics_line,
    values_kept_across_commits,
    without_doubled_words,
)
from app.llm import catalog
from app.llm.gateway import capture_models, complete_structured, using_model
from app.models import Chunk, Document, FlashcardSet, Job, MediaStatus
from app.retrieval.search import ChunkScope, retrieve

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
Do not write a card you cannot ground in a chunk. Cover different ideas; do not repeat one.

Stay on the TOPIC. When TOPICS lists several, write cards on every topic in TOPICS:
at least 2 cards on each topic in TOPICS, and about as many on each. Every card is about one of them: a chunk that is about something else (another
algorithm, an exercise, a summary of the chapter) is only there for context, so write no card from it.

When the chunks work through an example with figures (the waiting time of each process in a schedule, and their
average), at least one card works through it: the front gives the example's data and asks for the result, and the
back gives the steps and the result with the chunk's own figures, for example "P1 = 0, P2 = 24, P3 = 27, so the
average is (0 + 24 + 27)/3 = 17".

The back answers exactly what the front asks, and nothing else in its place: a front that asks about waiting time
and response time is answered with waiting time and response time, not turnaround time.
Check every card once more before you return it: the back answers its front, and no word is written twice in a
row ("việc việc")."""


def draft_cards(query: str, topics: str, chunks: list, language: OutputLanguage) -> "FlashcardDraft":
    """The one model call that writes the cards; scripts/injection_eval.py calls it with planted passages."""
    return complete_structured(
        f"TOPIC: {query}\n{topics}\n{format_source_chunks(chunks)}", FlashcardDraft,
        system_instruction=_INSTRUCTION + f"\n{term_rules(language)}" + output_language_instruction(language),
        temperature=0.2,
    )


def generate_flashcards(
    db: Session, flashcard_set_id: uuid.UUID, before_save: Callable[[], None] | None = None,
) -> None:
    """`before_save` runs right before the cards are written, in the transaction that writes them:
    the job queue checks there that its worker still holds the job (app/jobs/queue.py, hold)."""
    started = time.perf_counter()
    with values_kept_across_commits(db), timing.capture_timings() as spans, capture_models() as served:
        card_set = db.get(FlashcardSet, flashcard_set_id)
        if card_set is None:
            raise ValueError(f"Flashcard set {flashcard_set_id} not found")
        doc = db.get(Document, card_set.document_id)
        if doc is None or not doc.raw_text:
            raise ValueError("Tài liệu chưa xử lý xong.")
        language: OutputLanguage = card_set.output_language  # type: ignore[assignment]

        card_set.model_alias = catalog.served_or_default(card_set.model_alias)
        # Fail on an alias that has no judge of another family before any paid call.
        fact_check.judge_alias(card_set.model_alias)

        with using_model(card_set.model_alias):
            found = focus_passages(db, doc, card_set)
            chunks = found.chunks
            draft = draft_cards(found.query, topics_line(found), chunks, language)

        cards = []
        for item in draft.cards:
            cited = [resolve_source_id(chunks, code) for code in item.sources]
            used = list({c.id: c for c in cited if c is not None}.values())
            # A card whose source cannot be found cannot be checked or traced, so it is not kept.
            front, back = without_doubled_words(item.front.strip()), without_doubled_words(item.back.strip())
            if not used or not front or not back:
                continue
            cards.append((front, back, used))
        cards = cards[:MAX_CARDS]

        flags = fact_check.check_against_sources(
            [(f"{front}\n{back}", [c.content for c in used]) for front, back, used in cards],
            card_set.model_alias, language,
        )
        stored = [
            {"front": front, "back": back, "citations": [str(c.id) for c in used],
             "author": "model", "flag": flag}
            for (front, back, used), flag in zip(cards, flags, strict=True)
        ]

        if before_save is not None:
            before_save()
        card_set.cards = stored
        # The wall time too, as a module keeps it: how long a lecturer waited for the set.
        card_set.cost = {**llm_cost(spans), "seconds": round(time.perf_counter() - started, 1)}
        card_set.served_models = list(served)
        card_set.status = MediaStatus.draft
        db.commit()


class RevisedFlashcards(BaseModel):
    reply: str = Field(description="One or two sentences to the lecturer: what was changed, or why nothing was")
    cards: list[CardDraft]


_REVISE_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + f"""You revise a set of flashcards at the request of the lecturer who owns it.
You get the CURRENT CARDS, each with the IDs of the chunks it is taken from, the source chunks, and the REQUEST.
Return the whole set as it should be after the request: change, add, remove or reorder only what the request asks,
and copy every other card exactly as it is, with the same sources. At most {MAX_CARDS} cards.
A card you write or change follows the same rules as the others: the front asks a short question or names a term,
the back answers it in one to three sentences using ONLY what the source chunks say, and `sources` lists the IDs
of the chunks the back is taken from. Do not write a card you cannot ground in a chunk.
If the request cannot be done from the chunks, or is not about these cards, return the cards unchanged and say why
in `reply`. Write `reply` in the language of the cards."""


def revise_flashcards(db: Session, card_set: FlashcardSet, request: str) -> tuple[str, list[dict]]:
    """The set's cards after the lecturer's request, and what the model says it did. Cards it copies
    unchanged keep their author and check; a card it writes is fact-checked like a generated one."""
    doc = db.get(Document, card_set.document_id)
    if doc is None:
        raise ValueError("Tài liệu không còn.")
    language: OutputLanguage = card_set.output_language  # type: ignore[assignment]
    alias = catalog.served_or_default(card_set.model_alias)
    fact_check.judge_alias(alias)
    # The passages the cards already cite, then those the request is about: a new card may need either.
    cited_ids = list(dict.fromkeys(c for card in card_set.cards for c in card["citations"]))
    by_id = {str(c.id): c for c in db.query(Chunk).filter(
        Chunk.document_id == doc.id, Chunk.id.in_([uuid.UUID(c) for c in cited_ids])).all()} if cited_ids else {}
    chunks = [by_id[c] for c in cited_ids if c in by_id]
    known = set(by_id)
    chunks += [c for c in retrieve(db, doc.course_id, request, top_k=8, scope=ChunkScope(doc.id), wait=False) if str(c.id) not in known]
    code = {str(c.id): f"S{i + 1}" for i, c in enumerate(chunks)}
    current = "\n".join(
        f"{i + 1}. FRONT: {card['front']}\n   BACK: {card['back']}\n   SOURCES: {[code[c] for c in card['citations'] if c in code]}"
        for i, card in enumerate(card_set.cards))
    with using_model(alias):
        revised = complete_structured(
            f"CURRENT CARDS:\n{current}\n\n{format_source_chunks(chunks)}\n\nREQUEST: {request}", RevisedFlashcards,
            system_instruction=_REVISE_INSTRUCTION + f"\n{term_rules(language)}" + output_language_instruction(language),
            temperature=0.2,
        )

    before = {(card["front"], card["back"], tuple(card["citations"])): card for card in card_set.cards}
    kept, fresh = [], []
    for item in revised.cards[:MAX_CARDS]:
        used = list({c.id: c for c in (resolve_source_id(chunks, s) for s in item.sources) if c is not None}.values())
        front, back = without_doubled_words(item.front.strip()), without_doubled_words(item.back.strip())
        if not used or not front or not back:
            continue
        citations = [str(c.id) for c in used]
        same = before.get((front, back, tuple(citations)))
        kept.append(same)
        if same is None:
            fresh.append((len(kept) - 1, front, back, citations, [c.content for c in used]))
    flags = fact_check.check_against_sources([(f"{f}\n{b}", texts) for _, f, b, _, texts in fresh], alias, language)
    for (index, front, back, citations, _), flag in zip(fresh, flags, strict=True):
        kept[index] = {"front": front, "back": back, "citations": citations, "author": "model", "flag": flag}
    return revised.reply.strip(), kept


def run_flashcard_step(job: Job) -> None:
    run_generation_step(job, "flashcard_set_id", generate_flashcards)
