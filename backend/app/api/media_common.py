"""What the lecturer's routes for flashcard sets and for videos share: the generation request, the
ownership check, the passages a citation names, and the snippet a source picker shows for one."""
import json
import re
import uuid
from collections import Counter
from collections.abc import Iterable
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import is_course_author, require_author
from app.citations import citation_label
from app.generation.common import OutputLanguage
from app.ingestion.chunker import context_line, document_title, locator_heading
from app.models import Chunk, Course, Document, FlashcardSet, User, Video

# What an item the lecturer wrote without a source is flagged with.
NO_SOURCE_FLAG = "Chưa có nguồn"


class GenerateRequest(BaseModel):
    focus: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None
    output_language: OutputLanguage = "vi"
    model_alias: str | None = None


def owned_media[Media: (FlashcardSet, Video)](
    db: Session, model: type[Media], item_id: uuid.UUID, user: User, *, not_found: str, lock: bool = False,
) -> Media:
    require_author(user)
    # populate_existing: an item read before, as a change the model took seconds over reads it, is read
    # again under the lock; without it the lock leaves the old values and a save meanwhile is lost.
    item = db.get(model, item_id, with_for_update=lock, populate_existing=lock)
    if item is None:
        raise HTTPException(status_code=404, detail=not_found)
    # Both a missing item and someone else's answer 404, so an id does not confirm itself.
    course = db.get(Course, item.course_id)
    if course is None or not is_course_author(db, course, user):
        raise HTTPException(status_code=404, detail=not_found)
    return item


def require_own_chunks(db: Session, document_id: uuid.UUID, cited: set[uuid.UUID]) -> None:
    """A citation names a passage of the item's own document, or the source shown would be another's."""
    known = set(db.scalars(select(Chunk.id).where(Chunk.document_id == document_id, Chunk.id.in_(cited)))) if cited else set()
    if cited - known:
        raise HTTPException(status_code=422, detail="Trích nguồn phải là đoạn của chính tài liệu này.")


def chunk_passages(db: Session, document_id: uuid.UUID, chunk_ids: Iterable[str]) -> dict[str, dict]:
    """{chunk id: {chunk_id, locator, content}} for the cited chunks of the document that still exist.

    One query for all of them, so a set of 15 cards or a video of 10 scenes is not that many round
    trips. A chunk that no longer exists (the document was re-chunked) simply has no source to show."""
    wanted = set()
    for chunk_id in chunk_ids:
        try:
            wanted.add(uuid.UUID(str(chunk_id)))
        except ValueError:
            continue
    if not wanted:
        return {}
    filename = db.scalar(select(Document.filename).where(Document.id == document_id)) or ""
    chunks = db.scalars(select(Chunk).where(Chunk.document_id == document_id, Chunk.id.in_(wanted)))
    boilerplate = _document_boilerplate(db, document_id, filename)
    return {str(c.id): _passage(filename, c, boilerplate) for c in chunks}


SNIPPET_CHARS = 160
# How much of what comes before a match the snippet keeps, so the match is read in its sentence.
_SNIPPET_LEAD = 40
# A running header sits in the first lines of a page; a line this short is a label, not a header.
_HEAD_LINES = 4
_MIN_HEADER_CHARS = 4
# Enough passages to tell a header repeated on every page from two passages that start alike.
_MIN_PASSAGES_FOR_HEADER = 3
# Reading the head of this many passages is enough to see the header, however long the document.
_BOILERPLATE_SAMPLE = 200


def chunk_context_line(filename: str, content: str, source_ref: str | None) -> str:
    """The "document › section" line the chunker put at the head of the passage, rebuilt the way
    the chunker built it, so it is removed exactly and a passage without one keeps its first line."""
    try:
        locator = json.loads(source_ref or "")
    except ValueError:
        locator = {}
    heading = locator_heading(locator) if isinstance(locator, dict) and locator else None
    first, _, rest = content.partition("\n")
    line = context_line(document_title(filename), heading, rest)
    return line if line and first == line else ""


def _body(content: str, context: str) -> str:
    return content[len(context) + 1:] if context and content.startswith(context + "\n") else content


def repeated_head_lines(contents: list[str], context_lines: list[str]) -> set[str]:
    """The lines a PDF prints at the head of every page ("Operating System Concepts", the authors):
    those starting at least half of the document's passages, words only, not page numbers."""
    if len(contents) < _MIN_PASSAGES_FOR_HEADER:
        return set()
    counts: Counter[str] = Counter()
    for content, context in zip(contents, context_lines, strict=True):
        head = [line.strip() for line in _body(content, context).split("\n")[:_HEAD_LINES]]
        counts.update({line for line in head if len(line) >= _MIN_HEADER_CHARS and not line.isdigit()})
    needed = max(_MIN_PASSAGES_FOR_HEADER, -(-len(contents) // 2))
    return {line for line, n in counts.items() if n >= needed}


def _document_boilerplate(db: Session, document_id: uuid.UUID, filename: str) -> set[str]:
    rows = db.execute(
        select(func.left(Chunk.content, 400), Chunk.source_ref).where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index).limit(_BOILERPLATE_SAMPLE)).all()
    return repeated_head_lines([content for content, _ in rows],
                               [chunk_context_line(filename, content, ref) for content, ref in rows])


def passage_snippet(content: str, context: str, boilerplate: set[str], q: str | None) -> str:
    """What the source picker shows for a passage. Without this, every snippet of a lecture PDF
    began with its context line and the page's running header, so the line was used up before any
    of the passage's own words. With a search, the snippet is built around the first match."""
    kept: list[str] = []
    for line in (line.strip() for line in _body(content, context).split("\n")):
        if line in boilerplate:
            # The page number printed with the header is part of it.
            if kept and kept[-1].isdigit():
                kept.pop()
            continue
        kept.append(line)
    text = re.sub(r"\s+", " ", " ".join(kept)).strip()
    start, prefix = 0, ""
    match = re.search(re.escape(q.strip()), text, re.IGNORECASE) if q and q.strip() else None
    if match and match.start() > _SNIPPET_LEAD:
        start = match.start() - _SNIPPET_LEAD
        space = text.find(" ", start, match.start())
        start, prefix = (space + 1 if space != -1 else start), "…"
    window = text[start:]
    if len(prefix) + len(window) <= SNIPPET_CHARS:
        return prefix + window
    limit = SNIPPET_CHARS - len(prefix) - 1
    cut = window[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return prefix + cut.rstrip() + "…"


def _passage(filename: str, chunk: Chunk, boilerplate: set[str], q: str | None = None) -> dict:
    # Stored locators are JSON; every editor shows the same readable label built from them.
    context = chunk_context_line(filename, chunk.content, chunk.source_ref)
    return {"chunk_id": str(chunk.id), "locator": citation_label(filename, chunk.source_ref or ""),
            "content": chunk.content, "snippet": passage_snippet(chunk.content, context, boilerplate, q)}


def document_passages(db: Session, document: Document, q: str | None, offset: int, limit: int) -> dict:
    """A page of the document's passages in reading order, for the editors' source picker. `q` is a
    case-insensitive substring of the text, taken literally: a "%" the lecturer types is a "%"."""
    where = [Chunk.document_id == document.id]
    if q and q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append(Chunk.content.ilike(f"%{escaped}%", escape="\\"))
    total = db.scalar(select(func.count()).select_from(Chunk).where(*where)) or 0
    chunks = db.scalars(select(Chunk).where(*where).order_by(Chunk.chunk_index, Chunk.id).offset(offset).limit(limit))
    boilerplate = _document_boilerplate(db, document.id, document.filename)
    return {"items": [_passage(document.filename, c, boilerplate, q) for c in chunks], "total": total}
