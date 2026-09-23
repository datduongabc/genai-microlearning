"""Turns an outline and the chunk list into per-section chunk ranges."""

import bisect
from collections.abc import Sequence
from dataclasses import dataclass

from app.ingestion.outline import OutlineEntry


@dataclass(frozen=True)
class SectionSpec:
    position: int
    parent_position: int | None
    level: int
    title: str
    page_start: int | None
    page_end: int | None
    first_chunk_index: int
    last_chunk_index: int


def chunk_anchor(locator: dict) -> int | None:
    """Where a chunk starts, in the same terms as OutlineEntry.anchor."""
    if "unit_index" in locator:
        return locator["unit_index"]
    if "page" in locator:
        return locator["page"]
    spans = locator.get("spans")
    if spans:
        return spans[0].get("page")
    return None


def _monotone_anchors(anchors: Sequence[int | None]) -> list[int]:
    # Chunks come out in reading order, so a chunk never starts before the one
    # ahead of it. Enforcing that here keeps every section's chunks one
    # contiguous run whatever the input, which is what lets sibling ranges
    # stay disjoint. A chunk with no anchor travels with the chunk before it;
    # before any anchor exists it sits ahead of every section (-1 is below all).
    filled: list[int] = []
    previous = -1
    for anchor in anchors:
        if anchor is not None:
            previous = max(previous, anchor)
        filled.append(previous)
    return filled


def assign_chunk_ranges(
    entries: Sequence[OutlineEntry], chunk_anchors: Sequence[int | None], *, anchors_are_pages: bool
) -> list[SectionSpec]:
    """A section starts at its own anchor and ends before the next section of
    the same or a higher level, so a parent covers all of its children.

    A chunk belongs to the section that contains the page (or unit) it STARTS
    in: a chunk can span two pages, so a boundary can be off by one chunk, which
    is accepted. Sections left with no chunk are dropped. `entries` must be in
    document order, as clean_outline returns them.
    """
    anchors = _monotone_anchors(chunk_anchors)

    kept: list[tuple[OutlineEntry, int, int, int | None]] = []
    for index, entry in enumerate(entries):
        boundary = next((later.anchor for later in entries[index + 1 :] if later.level <= entry.level), None)
        first = bisect.bisect_left(anchors, entry.anchor)
        end = len(anchors) if boundary is None else bisect.bisect_left(anchors, boundary)
        if first < end:
            kept.append((entry, first, end - 1, boundary))

    specs: list[SectionSpec] = []
    ancestors: list[int] = []
    for position, (entry, first, last, boundary) in enumerate(kept):
        while ancestors and kept[ancestors[-1]][0].level >= entry.level:
            ancestors.pop()
        parent_position = ancestors[-1] if ancestors else None
        ancestors.append(position)

        if anchors_are_pages:
            page_start = entry.anchor
            page_end = max(page_start, boundary - 1) if boundary is not None else anchors[last]
        else:
            page_start = page_end = None
        specs.append(
            SectionSpec(position, parent_position, entry.level, entry.title, page_start, page_end, first, last)
        )
    return specs
