"""Detects the chapter/section outline of a textbook without any LLM.

Sources, in order of trust: the bookmarks stored in a PDF, the font size of
heading lines in a PDF without bookmarks, and the heading levels the DOCX and
HTML readers already record. A book that offers none of these has no outline
and keeps the whole-document flow.
"""

import logging
import os
from collections import Counter
from dataclasses import dataclass

import pymupdf

from app.ingestion.document_readers import DocumentUnit

logger = logging.getLogger(__name__)

MAX_LEVEL = 3
# One heading is a title, not a structure the teacher can choose from.
MIN_SECTIONS = 2
HEADING_SIZE_RATIO = 1.2
MAX_HEADING_CHARS = 120
# Same idea as the boilerplate rule in read_pdf: text that repeats on many
# pages is a running header or footer, not a heading.
RUNNING_HEADER_MIN_PAGES = 3
RUNNING_HEADER_PAGE_SHARE = 0.3


@dataclass(frozen=True)
class OutlineEntry:
    level: int
    title: str
    # PDF: 1-based page number. DOCX/HTML: index of the unit where the heading starts.
    anchor: int


def clean_outline(entries: list[OutlineEntry]) -> list[OutlineEntry]:
    cleaned: list[OutlineEntry] = []
    for entry in entries:
        # A NUL byte in a bookmark or heading would fail the whole embed step:
        # Postgres text columns refuse it (same reason read_document strips it).
        title = " ".join(entry.title.replace("\x00", "").split())
        if not title or not 1 <= entry.level <= MAX_LEVEL:
            continue
        if cleaned and cleaned[-1].title == title and cleaned[-1].anchor == entry.anchor:
            continue
        cleaned.append(OutlineEntry(entry.level, title, entry.anchor))
    # Stable sort: entries on the same page keep their document order, so a
    # chapter stays ahead of the first section on its own page.
    cleaned.sort(key=lambda e: e.anchor)
    return cleaned if len(cleaned) >= MIN_SECTIONS else []


def _pdf_toc_outline(path: str) -> list[OutlineEntry]:
    doc = pymupdf.open(path)
    try:
        toc = doc.get_toc()
    finally:
        doc.close()
    # A page below 1 marks a bookmark that points nowhere, so it cannot anchor a section.
    return [OutlineEntry(level, title, page) for level, title, page in toc if page >= 1]


def _pdf_font_outline(path: str) -> list[OutlineEntry]:
    doc = pymupdf.open(path)
    try:
        page_count = doc.page_count
        lines: list[tuple[int, float, str]] = []
        chars_by_size: Counter[float] = Counter()
        for page_number, page in enumerate(doc, start=1):
            # Without image blocks: a picture is never a heading, and extracting
            # its bytes made a 73-page slide deck take 8s instead of 0.1s.
            for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]:
                for line in block.get("lines", []):
                    spans = [s for s in line["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    text = " ".join("".join(s["text"] for s in spans).split())
                    size = round(max(s["size"] for s in spans), 1)
                    chars_by_size[size] += len(text)
                    lines.append((page_number, size, text))
    finally:
        doc.close()
    if not lines:
        return []

    body_size = chars_by_size.most_common(1)[0][0]

    def looks_like_heading(size: float, text: str) -> bool:
        return (
            size >= body_size * HEADING_SIZE_RATIO
            and len(text) <= MAX_HEADING_CHARS
            and not text.replace(" ", "").isdigit()
        )

    pages_by_text: dict[str, set[int]] = {}
    for page, size, text in lines:
        if looks_like_heading(size, text):
            pages_by_text.setdefault(text, set()).add(page)
    running = {
        text
        for text, pages in pages_by_text.items()
        if len(pages) >= RUNNING_HEADER_MIN_PAGES and len(pages) / page_count > RUNNING_HEADER_PAGE_SHARE
    }

    # A title that wraps arrives as consecutive lines of one size, and taking
    # each line as its own section would split "Chapter 10.A:" from "File-System
    # Interface". Running headers are dropped first, so one sitting next to a
    # heading in the same size can never be glued onto it.
    headings: list[tuple[int, float, str]] = []
    previous_kept = -1
    for index, (page, size, text) in enumerate(lines):
        if not looks_like_heading(size, text) or text in running:
            continue
        if headings and previous_kept == index - 1:
            last_page, last_size, last_text = headings[-1]
            joined = f"{last_text} {text}"
            if last_page == page and last_size == size and len(joined) <= MAX_HEADING_CHARS:
                headings[-1] = (page, size, joined)
                previous_kept = index
                continue
        headings.append((page, size, text))
        previous_kept = index

    sizes = sorted({size for _page, size, _text in headings}, reverse=True)[:MAX_LEVEL]
    level_of = {size: rank for rank, size in enumerate(sizes, start=1)}
    return [OutlineEntry(level_of[size], text, page) for page, size, text in headings if size in level_of]


def _units_outline(units: list[DocumentUnit]) -> list[OutlineEntry]:
    return [
        OutlineEntry(u.locator["heading_level"], u.locator["heading"], index)
        for index, u in enumerate(units)
        if u.locator.get("heading") and u.locator.get("heading_level")
    ]


def extract_outline(path: str, units: list[DocumentUnit]) -> list[OutlineEntry]:
    """Best effort: an outline problem must never fail the upload, so any
    error is logged and treated as "no outline"."""
    extension = os.path.splitext(path)[1].lower()
    try:
        if extension == ".pdf":
            return clean_outline(_pdf_toc_outline(path)) or clean_outline(_pdf_font_outline(path))
        if extension in (".docx", ".html", ".htm"):
            return clean_outline(_units_outline(units))
    except Exception:
        logger.warning("[outline] could not detect the outline of %s", path, exc_info=True)
    return []
