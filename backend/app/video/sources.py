"""The source line at the foot of a slide: the document and where in it the scene's passages are.

Shared by the render job and the lecturer's slide preview, so that the preview shows the line the
video will print.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.citations import location_parts, locator_spans
from app.generation.video_script import Scene
from app.models import Chunk, Document

_WORD = {"vi": "Nguồn", "en": "Source"}


def source_label(filename: str, locators: Sequence[str], lang: str = "vi") -> str:
    """"Nguồn: Chapter 5.pdf, trang 9–10" for the passages at `locators`, their pages merged.
    Empty when the scene cites nothing: a slide claims no source it does not have.

    No heading: a heading names one passage's section, and the foot of a slide is one line."""
    if not locators:
        return ""
    spans = [span for locator in locators for span in (locator_spans(locator) or [])]
    where = ", ".join(location_parts(spans, lang))
    return f"{_WORD.get(lang, _WORD['en'])}: {filename}" + (f", {where}" if where else "")


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def scene_source_labels(db: Session, video, scenes: Sequence[Scene]) -> list[str]:
    """The source line of each scene, in order. Only passages of the video's own document count, so
    a chunk id from elsewhere names nothing on the slide."""
    wanted = {cid for s in scenes for cid in (_uuid(c) for c in s.citations) if cid is not None}
    filename = db.scalar(select(Document.filename).where(Document.id == video.document_id)) or ""
    locators: dict[str, str] = {}
    if wanted:
        rows = db.execute(select(Chunk.id, Chunk.source_ref).where(
            Chunk.id.in_(wanted), Chunk.document_id == video.document_id))
        locators = {str(chunk_id): source_ref or "" for chunk_id, source_ref in rows}
    labels = []
    for s in scenes:
        cited = dict.fromkeys(str(cid) for cid in (_uuid(c) for c in s.citations) if cid is not None)
        labels.append(source_label(filename, [locators[c] for c in cited if c in locators], video.output_language))
    return labels
