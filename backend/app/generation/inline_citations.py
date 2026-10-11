"""Server-owned source maps and citations derived from the saved Content markers."""

import re

from pydantic import BaseModel

from app.generation.common import source_id
from app.models import Chunk

MARKER = re.compile(r"\[(S[0-9]+)\]")
CITATION_LIKE = re.compile(r"\[\s*S[0-9]+[^\]]*\]", re.IGNORECASE)
MARKER_GROUP = re.compile(r"\[S[0-9]+\](?:[ \t]*\[S[0-9]+\])*")


class CitationSource(BaseModel):
    chunk_id: str
    locator: str


class InlineCitation(CitationSource):
    source_id: str
    claim: str
    start: int
    end: int
    marker_start: int
    marker_end: int


def prompt_source_map(chunks: list[Chunk]) -> dict[str, CitationSource]:
    return {source_id(i): CitationSource(chunk_id=str(c.id), locator=c.source_ref or "")
            for i, c in enumerate(chunks)}


def unknown_source_codes(markdown: str, source_map: dict) -> list[str]:
    unknown = []
    for candidate in CITATION_LIKE.finditer(markdown):
        marker = MARKER.fullmatch(candidate[0])
        code = marker[1] if marker else candidate[0]
        if marker is None or code not in source_map:
            unknown.append(code)
    return list(dict.fromkeys(unknown))


def derive_citations(markdown: str, source_map: dict) -> list[InlineCitation]:
    """Each marker group cites the preceding sentence on its line, or the bullet fragment.

    Codes are reserved literal markers, case-sensitive, one code per pair of brackets.
    Adjacent codes share a span; an uncited earlier sentence is never silently included.
    Character spans are offsets into the saved Markdown, not a model's copied claim text.
    """
    unknown = unknown_source_codes(markdown, source_map)
    if unknown:
        raise ValueError(f"Content cites unknown source IDs: {', '.join(unknown)}")
    citations = []
    previous_end = 0
    for group in MARKER_GROUP.finditer(markdown):
        start = max(previous_end, markdown.rfind("\n", 0, group.start()) + 1)
        prefix = markdown[start:group.start()].rstrip()
        # Keep the last sentence only. A trailing full stop belongs to that sentence.
        boundaries = list(re.finditer(r"(?<=[.!?。！？])\s+(?=\S)", prefix))
        if boundaries:
            start += boundaries[-1].end()
            prefix = markdown[start:group.start()].rstrip()
        decoration = re.match(r"\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)?", prefix)
        start += decoration.end()
        end = start + len(markdown[start:group.start()].rstrip())
        claim = markdown[start:end]
        if not claim.strip():
            raise ValueError("Citation marker must follow a sentence or bullet")
        for marker in MARKER.finditer(markdown, group.start(), group.end()):
            source = CitationSource.model_validate(source_map[marker[1]])
            citations.append(InlineCitation(**source.model_dump(), source_id=marker[1], claim=claim,
                                           start=start, end=end, marker_start=marker.start(), marker_end=marker.end()))
        previous_end = group.end()
    return citations


def citation_content(markdown: str, source_map: dict) -> dict:
    citations = derive_citations(markdown, source_map)
    return {"markdown": markdown,
            "source_map": {key: CitationSource.model_validate(value).model_dump() for key, value in source_map.items()},
            "inline_citations": [c.model_dump() for c in citations],
            "source_locators": list(dict.fromkeys(c.locator for c in citations if c.locator))}
