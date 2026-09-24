import bisect
import math
import re
from dataclasses import dataclass
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.ingestion.document_readers import DocumentUnit

# Recursive splitting (text/markdown-ish units: HTML, DOCX, plain .txt
# paragraphs) -- mục 1 kiến trúc: chunk_size 400-512 token, overlap 10-20%.
# Tokens approximated as chars/4 (same approximation chunker uses for
# token_count below) -- good enough for a size target, not an exact tokenizer.
RECURSIVE_CHUNK_CHARS = 2000
RECURSIVE_OVERLAP_CHARS = 300

# Page/slide-level chunking (PDF/PPTX units).
MAX_UNITS_PER_CHUNK = 2
PAGE_CHUNK_TARGET_CHARS = 1000
PAGE_OVERLAP_CHARS = 150

# Sentence/caption-level chunking (video/audio transcripts with timestamps).
TRANSCRIPT_CHUNK_CHARS = 600


@dataclass
class Chunk:
    content: str
    chunk_index: int
    locator: dict[str, Any]
    course_id: str
    token_count: int
    extraction_method: str = "text_layer"
    strategy: str = "recursive"


def _classify(unit: DocumentUnit) -> str:
    loc = unit.locator
    if "page" in loc or "slide_index" in loc:
        return "page_level"
    if "start_ms" in loc:
        return "sentence"
    return "recursive"


def document_title(filename: str) -> str:
    """Tên tài liệu đọc được từ tên tệp: bỏ đuôi và dấu gạch dưới. Giảng viên đặt tên tệp theo
    bài ("Chương 4a - Tích phân bất định"), nên đây là nhãn ngữ cảnh rẻ và đúng nhất có sẵn."""
    stem = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return re.sub(r"\s+", " ", stem.replace("_", " ")).strip()


def _locator_heading(locator: dict) -> str | None:
    first = locator["spans"][0] if locator.get("spans") else locator
    return first.get("heading") or first.get("slide_title") or None


def context_line(title: str | None, heading: str | None, content: str) -> str:
    """Dòng ngữ cảnh đặt đầu chunk: "tài liệu › mục". Một chunk giữa bài chỉ còn lại vài công
    thức hay vài gạch đầu dòng, không có chữ nào nói nó thuộc bài nào, trong khi truy hồi tìm
    trên cả môn học gồm nhiều bài. Tiêu đề mục bị bỏ khi chunk đã mở đầu bằng chính nó."""
    parts = [title] if title else []
    heading = heading.strip() if heading else ""
    redundant = bool(title) and heading.casefold() in title.casefold()
    if heading and not redundant and not content.lstrip().startswith(heading):
        parts.append(heading)
    return " › ".join(parts)


def chunk_units(units: list[DocumentUnit], title: str | None = None) -> list[Chunk]:
    """Dispatch each contiguous run of same-strategy units (per mục 1 kiến
    trúc: recursive cho text/markdown, page-level cho PDF/PPTX, sentence-based
    cho transcript) to its chunking function. A single `read_document()` call
    always produces one shape of unit, so in practice this is one run; the
    grouping just stays correct if units from different readers are ever
    combined into one call.
    """
    chunks: list[Chunk] = []
    current_strategy: str | None = None
    current_group: list[DocumentUnit] = []
    group_start = 0

    def flush_group():
        if not current_group:
            return
        if current_strategy == "page_level":
            _chunk_page_level(current_group, chunks)
        elif current_strategy == "sentence":
            _chunk_transcript(current_group, chunks)
        else:
            _chunk_recursive(current_group, chunks, first_unit_index=group_start)

    for index, unit in enumerate(units):
        strategy = _classify(unit)
        if current_strategy is not None and strategy != current_strategy:
            flush_group()
            current_group = []
            group_start = index
        current_strategy = strategy
        current_group.append(unit)
    flush_group()
    for chunk in chunks:
        line = context_line(title, _locator_heading(chunk.locator), chunk.content)
        if line:
            chunk.content = f"{line}\n{chunk.content}"
            chunk.token_count = math.ceil(len(chunk.content) / 4)
    return chunks


def _make_chunk(
    chunks: list[Chunk], content: str, locator: dict, course_id: str,
    extraction_method: str, strategy: str,
) -> None:
    chunks.append(Chunk(
        content=content,
        chunk_index=len(chunks),
        locator=locator,
        course_id=course_id,
        token_count=math.ceil(len(content) / 4),
        extraction_method=extraction_method,
        strategy=strategy,
    ))


def _chunk_recursive(units: list[DocumentUnit], chunks: list[Chunk], first_unit_index: int = 0) -> None:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=RECURSIVE_CHUNK_CHARS,
        chunk_overlap=RECURSIVE_OVERLAP_CHARS,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    # Positions are kept against the ORIGINAL unit list (empty units included):
    # the outline of a DOCX/HTML textbook refers to units by that position.
    kept = [(first_unit_index + i, u) for i, u in enumerate(units) if u.text.strip()]
    text = "\n\n".join(u.text for _, u in kept)
    if not text.strip():
        return
    unit_starts: list[int] = []
    position = 0
    for _, u in kept:
        unit_starts.append(position)
        position += len(u.text) + len("\n\n")
    course_id = units[0].course_id
    by_position = dict(kept)
    cursor = 0
    unit_index = kept[0][0]
    for piece in splitter.split_text(text):
        # Pieces come out in order, so searching from the previous start finds
        # where this one begins; a miss keeps the previous unit, which is at
        # worst one unit early and never goes backwards.
        found = text.find(piece, cursor)
        if found != -1:
            cursor = found
            unit_index = kept[bisect.bisect_right(unit_starts, found) - 1][0]
        # Tiêu đề của mục chứa đoạn này, không phải của mục đầu tài liệu: trước đây mọi chunk của
        # một giáo trình DOCX đều mang tiêu đề chương 1.
        heading = by_position[unit_index].locator.get("heading")
        _make_chunk(
            chunks, piece, {"heading": heading, "unit_index": unit_index}, course_id, "text_layer", "recursive"
        )


def _split_long_text(text: str, target_chars: int, overlap_chars: int) -> list[str]:
    """Character-based sliding window for a single oversized unit (e.g. a
    very text-dense page) -- every piece capped at target_chars, overlapping
    its neighbor by exactly overlap_chars, regardless of paragraph length.
    """
    text = text.strip()
    if not text:
        return []
    step = target_chars - overlap_chars if target_chars > overlap_chars else target_chars
    pieces: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + target_chars, n)
        piece = text[start:end]
        if piece.strip():
            pieces.append(piece)
        if end >= n:
            break
        start += step
    return pieces


def _group_key(locator: dict) -> Any:
    return locator.get("page", locator.get("slide_index"))


def _chunk_page_level(units: list[DocumentUnit], chunks: list[Chunk]) -> None:
    course_id = units[0].course_id if units else ""
    buffer_text: list[str] = []
    buffer_locators: list[dict] = []

    def flush():
        if not buffer_text:
            return
        content = "\n".join(buffer_text)
        merged_locator = buffer_locators[0] if len(buffer_locators) == 1 else {"spans": list(buffer_locators)}
        # A chunk merged from multiple pages must not be mislabeled
        # "text_layer" when a later page in it actually came from OCR --
        # that's exactly the quality signal MAX_UNITS_PER_CHUNK exists to
        # preserve.
        methods = {loc.get("extraction_method", "text_layer") for loc in buffer_locators}
        methods.discard("text_layer")
        extraction_method = methods.pop() if len(methods) == 1 else ("mixed" if methods else "text_layer")
        _make_chunk(chunks, content, merged_locator, course_id, extraction_method, "page_level")
        buffer_text.clear()
        buffer_locators.clear()

    for unit in units:
        unit_key = _group_key(unit.locator)
        if buffer_text and unit_key is not None:
            buffer_keys = {_group_key(loc) for loc in buffer_locators if _group_key(loc) is not None}
            if buffer_keys and unit_key not in buffer_keys and len(buffer_keys | {unit_key}) > MAX_UNITS_PER_CHUNK:
                flush()

        if len(unit.text) >= PAGE_CHUNK_TARGET_CHARS:
            flush()
            for i, piece in enumerate(_split_long_text(unit.text, PAGE_CHUNK_TARGET_CHARS, PAGE_OVERLAP_CHARS)):
                _make_chunk(
                    chunks, piece, {**unit.locator, "part": i + 1}, unit.course_id,
                    unit.locator.get("extraction_method", "text_layer"), "page_level",
                )
            continue

        buffer_text.append(unit.text)
        buffer_locators.append(unit.locator)
        if sum(len(t) for t in buffer_text) >= PAGE_CHUNK_TARGET_CHARS:
            flush()

    flush()


def _chunk_transcript(units: list[DocumentUnit], chunks: list[Chunk]) -> None:
    course_id = units[0].course_id if units else ""
    buffer_text: list[str] = []
    buffer_start: int | None = None
    buffer_end: int | None = None

    def flush():
        nonlocal buffer_start, buffer_end
        if not buffer_text:
            return
        content = " ".join(buffer_text)
        _make_chunk(
            chunks, content, {"start_ms": buffer_start, "end_ms": buffer_end},
            course_id, "text_layer", "sentence",
        )
        buffer_text.clear()
        buffer_start = buffer_end = None

    for unit in units:
        start_ms = unit.locator.get("start_ms")
        end_ms = unit.locator.get("end_ms")

        # A single caption/segment can itself be far larger than the target
        # (e.g. a mis-segmented ASR transcript that dumps an entire lecture
        # into one "caption" spanning a couple of seconds) -- split it on
        # its own rather than emitting one giant chunk.
        if len(unit.text) >= TRANSCRIPT_CHUNK_CHARS:
            flush()
            for piece in _split_long_text(unit.text, TRANSCRIPT_CHUNK_CHARS, 0):
                _make_chunk(chunks, piece, {"start_ms": start_ms, "end_ms": end_ms}, course_id, "text_layer", "sentence")
            continue

        buffer_text.append(unit.text)
        buffer_start = start_ms if buffer_start is None else min(buffer_start, start_ms)
        buffer_end = end_ms if buffer_end is None else max(buffer_end, end_ms)
        if sum(len(t) for t in buffer_text) >= TRANSCRIPT_CHUNK_CHARS:
            flush()

    flush()
