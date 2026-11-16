"""What the generation of a flashcard set and of a video script share: the topic their passages are
retrieved for, the cleanup of the model's text, a session that does not reopen a transaction while a
model is answering, and the job step that runs one of them."""

import contextlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db import WorkerSessionLocal
from app.generation.module_generation_pipeline import _get_or_cache_planner
from app.jobs.queue import hold
from app.models import Chunk, Document, Job, JobStatus
from app.retrieval.search import ChunkScope
from app.retrieval.search import retrieve_for_generation as retrieve

# The passages of a focus naming several topics, all topics together: about what one adaptive
# retrieval gives for each of two topics, and what the trial video was written from (14).
MAX_FOCUS_CHUNKS = 16
# Fewer passages than this for a topic is too little to write about it.
MIN_CHUNKS_PER_QUERY = 8
_MAX_TOPICS = 4
# A part shorter than this ("Ưu" in "Ưu và nhược điểm của RR") is a word of the phrase, not a topic.
_MIN_TOPIC_CHARS = 3
_TOPIC_SEPARATOR = re.compile(r"\s*[,;]\s*|\s+(?:và|and|&|vs\.?|versus)\s+", re.IGNORECASE)


def focus_query(db: Session, doc: Document, item) -> str:
    """`item` is a FlashcardSet or a Video: both carry `focus` and `output_language`."""
    if item.focus and item.focus.strip():
        return item.focus.strip()
    # No focus: the topic the planner ranks first, as a module of this document would be about.
    concepts = _get_or_cache_planner(db, doc, item.output_language).concepts
    first = min(concepts, key=lambda c: c.priority)
    return f"{first.name}: {first.summary}"


def focus_topics(focus: str) -> list[str]:
    """The topics a lecturer's focus names when it names more than one ("FCFS và Round Robin"), or
    [] when it is one topic. What follows a colon describes the topics, so only what comes before
    it is split. A part too short to be a topic, or more parts than a list of topics would have,
    mean the focus is a phrase or a description, which is retrieved as it is."""
    head = focus.split(":", 1)[0]
    parts = [p.strip() for p in _TOPIC_SEPARATOR.split(head)]
    if len(parts) < 2 or len(parts) > _MAX_TOPICS or any(len(p) < _MIN_TOPIC_CHARS for p in parts):
        return []
    return parts


@dataclass
class FocusPassages:
    query: str
    # The topics the focus names, when it names several; the prompt asks for each of them.
    topics: list[str]
    chunks: list[Chunk]


def _merged(per_topic: list[list[Chunk]]) -> list[Chunk]:
    """Each topic's passages in turn, best first, so that no topic crowds the others out of the
    cap; then in reading order, so the passages read as the lecture does."""
    picked: dict = {}
    for rank in range(max(len(found) for found in per_topic)):
        for found in per_topic:
            if rank < len(found) and len(picked) < MAX_FOCUS_CHUNKS:
                picked.setdefault(found[rank].id, found[rank])
    return sorted(picked.values(), key=lambda c: c.chunk_index)


def focus_passages(db: Session, doc: Document, item) -> FocusPassages:
    """The passages a flashcard set or a video is written from. One query for "FCFS và Round
    Robin" ranked Round Robin and other scheduling first and left out FCFS's worked example, so a
    focus naming several topics is retrieved one topic at a time and merged."""
    query = focus_query(db, doc, item)
    db.commit()
    lecturer_focus = bool(item.focus and item.focus.strip())
    topics = focus_topics(query) if lecturer_focus else []
    scope = ChunkScope(doc.id)
    if not topics:
        chunks = retrieve(db, doc.course_id, query, top_k=None, min_k=MIN_CHUNKS_PER_QUERY, scope=scope)
    else:
        chunks = _merged([retrieve(db, doc.course_id, topic, top_k=None, min_k=MIN_CHUNKS_PER_QUERY, scope=scope)
                          for topic in topics])
    db.commit()
    return FocusPassages(query=query, topics=topics, chunks=chunks)


def topics_line(found: FocusPassages) -> str:
    """The line of a prompt that lists the topics, for the rule that every one of them is covered."""
    return f"TOPICS: {'; '.join(found.topics)}\n" if found.topics else ""


# Words Vietnamese doubles on purpose ("dần dần", "luôn luôn", "từ từ"), and English words that
# may stand twice in a correct sentence ("had had", "that that").
_DOUBLED_ON_PURPOSE = frozenset({
    "dần", "luôn", "từ", "thường", "ngày", "đêm", "người", "nhà", "nơi", "đâu", "ai", "gì", "mãi", "đời", "năm",
    "tháng", "lần", "chút", "khắp", "mọi", "rồi", "nhiều", "had", "that",
})
_DOUBLED = re.compile(r"\b([^\W\d_]+)(?:\s+\1\b)+", re.IGNORECASE)


def without_doubled_words(text: str) -> str:
    """A word the model wrote twice in a row ("ngoại trừ việc việc chia sẻ") written once. Only
    for the model's text: a lecturer's is theirs. Figures are left alone ("P2 3 3" is a table)."""
    def once(m: re.Match) -> str:
        return m[0] if m[1].casefold() in _DOUBLED_ON_PURPOSE else m[1]
    return _DOUBLED.sub(once, text)


@contextlib.contextmanager
def values_kept_across_commits(db: Session):
    """Read values stay in memory across the commits, so no prompt reopens a transaction by reading
    an expired attribute while a model is being called: that transaction would hold a database
    connection idle for as long as the model takes."""
    expire_on_commit = db.expire_on_commit
    db.expire_on_commit = False
    try:
        yield
    finally:
        db.expire_on_commit = expire_on_commit


def run_generation_step(
    job: Job, payload_key: str, generate: Callable[..., None],
) -> None:
    """Runs `generate(db, target_id, before_save=...)` for the item the job's payload names, in a
    session of its own. `before_save` marks the job done in the transaction that writes the result,
    so an item is never ready with its job still running, and a worker that lost the job writes
    nothing (app/jobs/queue.py, hold)."""
    target_id = uuid.UUID(job.payload[payload_key])
    db = WorkerSessionLocal()

    def finish() -> None:
        hold(db, job).status = JobStatus.done

    try:
        generate(db, target_id, before_save=finish)
    finally:
        db.close()
