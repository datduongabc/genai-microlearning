"""What the lecturer's routes for flashcard sets and for videos share: the generation request, the
ownership check, and the passages a citation names."""
import uuid
from collections.abc import Iterable
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_author
from app.citations import citation_label
from app.generation.common import OutputLanguage
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
    item = db.get(model, item_id, with_for_update=lock)
    if item is None:
        raise HTTPException(status_code=404, detail=not_found)
    # Both a missing item and someone else's answer 404, so an id does not confirm itself.
    course = db.get(Course, item.course_id)
    if course is None or course.owner_id != user.id:
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
    # Stored locators are JSON; every editor shows the same readable label built from them.
    return {str(c.id): {"chunk_id": str(c.id), "locator": citation_label(filename, c.source_ref or ""),
                        "content": c.content} for c in chunks}
