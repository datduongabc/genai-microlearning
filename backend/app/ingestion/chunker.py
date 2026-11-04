import bisect
import re
from dataclasses import dataclass
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.ingestion.document_readers import DocumentUnit

# Kích thước đo bằng token ước lượng (estimate_tokens), không bằng ký tự: cùng 1000 ký tự,
# văn bản tiếng Anh là khoảng 250 token còn slide toán tiếng Việt là hơn 500, nên một ngưỡng
# ký tự chung làm chunk toán dài gấp đôi chunk tiếng Anh. Ngân sách được đặt theo trần
# 512 token của BGE-base trước đây, chừa khoảng 50 token cho câu hỏi và 40 cho ngữ cảnh.
# Giữ ngân sách này khi đổi sang Jina để không đổi corpus đã nhúng.
MAX_CONTENT_TOKENS = 340

# Recursive splitting (DOCX, HTML, .txt) -- mục 1 kiến trúc: overlap 10-20%.
RECURSIVE_OVERLAP_TOKENS = 50

# Page/slide-level chunking (PDF/PPTX units): gộp tới khoảng một slide dày chữ.
MAX_UNITS_PER_CHUNK = 2
PAGE_CHUNK_TARGET_TOKENS = 250
PAGE_OVERLAP_TOKENS = 40

# Sentence/caption-level chunking (video/audio transcripts with timestamps).
TRANSCRIPT_CHUNK_TOKENS = 150

_WORD_RE = re.compile(r"\w+")
_SYMBOL_RE = re.compile(r"[^\w\s]")
_SEGMENT_RE = re.compile(r"\S+\s*")


def estimate_tokens(text: str) -> int:
    """Số token ước lượng: mỗi từ một token, thêm một cho mỗi bảy ký tự của từ dài, mỗi ký hiệu
    một token. Đo trên 1100 chunk của năm môn trong docs/documents, nó bám tokenizer thật của
    reranker trong khoảng 0,84-1,02 lần ở cả tiếng Anh lẫn toán tiếng Việt, trong khi chia 4 theo
    ký tự chỉ đúng với tiếng Anh và thấp một nửa với toán. Không nạp tokenizer thật vì chunker
    chạy cả trong test, nơi không có mô hình của reranker."""
    return sum(1 + len(w) // 7 for w in _WORD_RE.findall(text)) + len(_SYMBOL_RE.findall(text))


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
            chunk.token_count = estimate_tokens(chunk.content)
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
        token_count=estimate_tokens(content),
        extraction_method=extraction_method,
        strategy=strategy,
    ))


def _chunk_recursive(units: list[DocumentUnit], chunks: list[Chunk], first_unit_index: int = 0) -> None:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=MAX_CONTENT_TOKENS,
        chunk_overlap=RECURSIVE_OVERLAP_TOKENS,
        length_function=estimate_tokens,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    # Keep source unit positions stable even when empty units are skipped.
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


def _split_long_text(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Cửa sổ trượt theo từ cho một đơn vị quá dài (một trang dày chữ, một caption khổng lồ):
    mỗi mảnh không quá max_tokens, gối lên mảnh trước khoảng overlap_tokens. Cắt ở ranh giới từ
    để không mảnh nào mở đầu bằng nửa chữ."""
    segments = _SEGMENT_RE.findall(text.strip())
    if not segments:
        return []
    costs = [max(1, estimate_tokens(seg)) for seg in segments]
    pieces: list[str] = []
    start = 0
    while start < len(segments):
        end, total = start, 0
        while end < len(segments) and (end == start or total + costs[end] <= max_tokens):
            total += costs[end]
            end += 1
        pieces.append("".join(segments[start:end]).strip())
        if end >= len(segments):
            break
        back, kept = end, 0
        while back - 1 > start and kept + costs[back - 1] <= overlap_tokens:
            back -= 1
            kept += costs[back]
        start = back
    return [p for p in pieces if p]


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
        if not unit.text.strip():
            continue
        unit_key = _group_key(unit.locator)
        if buffer_text and unit_key is not None:
            buffer_keys = {_group_key(loc) for loc in buffer_locators if _group_key(loc) is not None}
            if buffer_keys and unit_key not in buffer_keys and len(buffer_keys | {unit_key}) > MAX_UNITS_PER_CHUNK:
                flush()

        tokens = estimate_tokens(unit.text)
        if tokens > MAX_CONTENT_TOKENS:
            flush()
            for i, piece in enumerate(_split_long_text(unit.text, MAX_CONTENT_TOKENS, PAGE_OVERLAP_TOKENS)):
                _make_chunk(
                    chunks, piece, {**unit.locator, "part": i + 1}, unit.course_id,
                    unit.locator.get("extraction_method", "text_layer"), "page_level",
                )
            continue
        # Gộp thêm trang này mà vượt trần thì chốt chunk trước: trước đây chỉ xét sau khi gộp,
        # nên hai trang vừa dưới ngưỡng thành một chunk dài gần gấp đôi.
        if buffer_text and sum(estimate_tokens(t) for t in buffer_text) + tokens > MAX_CONTENT_TOKENS:
            flush()

        buffer_text.append(unit.text)
        buffer_locators.append(unit.locator)
        if sum(estimate_tokens(t) for t in buffer_text) >= PAGE_CHUNK_TARGET_TOKENS:
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
        if estimate_tokens(unit.text) >= TRANSCRIPT_CHUNK_TOKENS:
            flush()
            for piece in _split_long_text(unit.text, TRANSCRIPT_CHUNK_TOKENS, 0):
                _make_chunk(chunks, piece, {"start_ms": start_ms, "end_ms": end_ms}, course_id, "text_layer", "sentence")
            continue

        buffer_text.append(unit.text)
        buffer_start = start_ms if buffer_start is None else min(buffer_start, start_ms)
        buffer_end = end_ms if buffer_end is None else max(buffer_end, end_ms)
        if sum(estimate_tokens(t) for t in buffer_text) >= TRANSCRIPT_CHUNK_TOKENS:
            flush()

    flush()
