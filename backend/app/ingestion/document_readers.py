import csv
import io
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

import pymupdf
import pytesseract
from bs4 import BeautifulSoup
from docx import Document as DocxDocument
from litellm.exceptions import RateLimitError
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.ingestion.pdf_layout import page_lines, title_candidates
from app.llm.gateway import transcribe_audio

logger = logging.getLogger(__name__)


@dataclass
class DocumentUnit:
    text: str
    locator: dict[str, Any] = field(default_factory=dict)
    course_id: str = ""
    source_id: str = ""


MIN_TEXT_LEN_PER_PAGE = 20
HEADING_RE = re.compile(r"^\s*(\d+(\.\d+)*)\.?\s+\S")


def read_pdf(path: str) -> list[DocumentUnit]:
    doc = pymupdf.open(path)
    try:
        raw_units = []
        for page_index, page in enumerate(doc):
            page_text = "\n".join(page_lines(page))
            titles = title_candidates(page)
            extraction_method = "text_layer"
            if len(page_text.strip()) < MIN_TEXT_LEN_PER_PAGE:
                try:
                    pix = page.get_pixmap(dpi=200)
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    page_text = pytesseract.image_to_string(img, lang="eng+vie")
                    extraction_method = "ocr"
                except Exception as e:
                    # OCR is best-effort per page -- a single bad/scanned page
                    # (or a misconfigured OCR toolchain) must not discard the
                    # text already successfully extracted from every other
                    # page in the document.
                    logger.warning(f"[document_readers] OCR failed on page {page_index + 1} of {path}: {e}")
                    page_text = ""
                    extraction_method = "ocr_failed"

            lines_all = page_text.split("\n")
            current_lines, current_heading = [], None

            def flush():
                if current_lines and "".join(current_lines).strip():
                    stripped = [line.strip() for line in current_lines if line.strip()]
                    raw_units.append({
                        "page": page_index + 1, "lines": stripped, "numbered": current_heading,
                        "titles": titles,
                        "extraction_method": extraction_method,
                    })

            for line in lines_all:
                if HEADING_RE.match(line):
                    flush()
                    current_lines, current_heading = [line], line.strip()
                else:
                    current_lines.append(line)
            flush()
    finally:
        doc.close()

    total = len(raw_units)
    if total == 0:
        return []
    pages = {u["page"] for u in raw_units}
    page_counts: dict[str, int] = {}
    for page in pages:
        titles = next((u["titles"] for u in raw_units if u["page"] == page and u["titles"]), [])
        for title in set(titles):
            page_counts[title] = page_counts.get(title, 0) + 1
    # Dòng to lặp lại trên nhiều trang là tiêu đề chung của cả bộ slide hay chân trang in to:
    # tiêu đề riêng của trang, nếu có, là dòng to kế tiếp.
    deck_titles = {t for t, c in page_counts.items() if c >= 3 and c / len(pages) > 0.2}

    units = []
    for u in raw_units:
        # Trang không có dòng nào đủ làm tiêu đề thì không có tiêu đề: dòng đầu tiên của trang có
        # thể là chân trang, số trang hay một gạch đầu dòng, và một nhãn sai làm truy hồi lệch hơn
        # là không có nhãn.
        heading = u["numbered"] or next((t for t in u["titles"] if t not in deck_titles), None)
        units.append(DocumentUnit(
            text="\n".join(u["lines"]),
            locator={"page": u["page"], "heading": heading, "extraction_method": u["extraction_method"]},
        ))
    return units


def _clean_pptx_text(text: str) -> str:
    return text.replace("\x0b", " ").strip()


def _iter_pptx_shapes(shapes):
    """Every shape of a slide in reading order, going into groups of shapes: the text of a group
    is not on the slide itself, so a loop over the slide alone never sees it."""
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_pptx_shapes(shape.shapes)
        else:
            yield shape


def _pptx_table_lines(table) -> list[str]:
    """One line per row, each cell with the heading of its column ("Process: P1; Burst Time: 24"):
    a value read without its heading (24 what?) says nothing to a reader who was not on the slide.
    A table of a single row has no heading to attach, so its cells are simply joined."""
    rows = [[_clean_pptx_text(cell.text) for cell in row.cells] for row in table.rows]
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    if not body:
        line = " | ".join(value for value in header if value)
        return [line] if line else []
    lines = []
    for row in body:
        cells = [f"{heading}: {value}" if heading else value for heading, value in zip(header, row, strict=False) if value]
        if cells:
            lines.append("; ".join(cells))
    return lines


def read_pptx(path: str) -> list[DocumentUnit]:
    """One unit per slide with any text: the title, then everything else on the slide in the order
    it was placed (text boxes, tables, shapes inside groups), then the speaker notes."""
    units = []
    prs = Presentation(path)
    for slide_index, slide in enumerate(prs.slides):
        title_shape = slide.shapes.title
        title, body = "", []
        for shape in _iter_pptx_shapes(slide.shapes):
            if shape.has_text_frame:
                text = _clean_pptx_text(shape.text_frame.text)
                if not text:
                    continue
                if title_shape is not None and shape == title_shape:
                    title = text
                else:
                    body.append(text)
            elif getattr(shape, "has_table", False) and shape.has_table:
                body.extend(_pptx_table_lines(shape.table))
        notes = ""
        if slide.has_notes_slide:
            notes = _clean_pptx_text(slide.notes_slide.notes_text_frame.text)
        parts = [title] if title else []
        parts.extend(body)
        if notes:
            parts.append(f"Ghi chú: {notes}")
        text_combined = "\n".join(parts)
        if text_combined.strip():
            units.append(DocumentUnit(
                text=text_combined,
                locator={"slide_index": slide_index + 1, "slide_title": title},
            ))
    return units


HEADING_TAGS = {"h1", "h2", "h3"}
PILCROW_RE = re.compile(r"\s*¶\s*$")


def _clean_html_text(el) -> str:
    text = el.get_text(" ", strip=True)
    text = PILCROW_RE.sub("", text)
    return re.sub(r"\s+", " ", text)


def read_html(path: str) -> list[DocumentUnit]:
    # Strict UTF-8 (no errors="ignore"): a legacy-encoded Vietnamese HTML
    # export (Windows-1258/TCVN3, common from older "Save as Web Page"
    # exports) would have every diacritic silently stripped otherwise,
    # reporting success with quietly garbled text. Fail loudly instead.
    with open(path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    units = []
    current_lines, current_heading, current_level = [], None, None

    def flush():
        if current_lines and "\n".join(current_lines).strip():
            locator = {"heading": current_heading}
            # Only units that open with a heading carry a level: the outline
            # of a textbook is built from exactly those units.
            if current_level is not None:
                locator["heading_level"] = current_level
            units.append(DocumentUnit(text="\n".join(current_lines).strip(), locator=locator))

    body = soup.body or soup
    for el in body.find_all([*HEADING_TAGS, "p"]):
        if el.name in HEADING_TAGS:
            flush()
            current_heading = _clean_html_text(el)
            current_level = int(el.name[1])
            current_lines = [current_heading]
        else:
            text = _clean_html_text(el)
            if text:
                current_lines.append(text)
    flush()
    return units


def _docx_heading_level(style_name: str) -> int:
    # "Heading 2" -> 2; a bare "Heading" style has no number and is a top-level heading.
    last = style_name.split()[-1]
    return int(last) if last.isdigit() else 1


def read_docx(path: str) -> list[DocumentUnit]:
    doc = DocxDocument(path)
    units: list[DocumentUnit] = []

    current_lines, current_heading, current_level = [], None, None

    def flush_para():
        if current_lines and "\n".join(current_lines).strip():
            locator = {"heading": current_heading}
            if current_level is not None:
                locator["heading_level"] = current_level
            units.append(DocumentUnit(text="\n".join(current_lines).strip(), locator=locator))

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if para.style.name.startswith("Heading"):
            flush_para()
            current_lines, current_heading = [text], text
            current_level = _docx_heading_level(para.style.name)
        else:
            current_lines.append(text)
    flush_para()

    def _is_merged_banner_row(cells):
        non_empty = [c for c in cells if c]
        if not non_empty:
            return True
        most_common_count = max(non_empty.count(v) for v in set(non_empty))
        return most_common_count / len(non_empty) > 0.5

    for table_index, table in enumerate(doc.tables):
        if len(table.rows) < 2:
            continue
        # header_row_idx must leave at least one row after it to serve as
        # data — bounding at len(rows) - 2, not - 1.
        header_row_idx = 0
        while header_row_idx < len(table.rows) - 2 and _is_merged_banner_row(
            [c.text.strip() for c in table.rows[header_row_idx].cells]
        ):
            header_row_idx += 1
        header_cells = [c.text.strip() for c in table.rows[header_row_idx].cells]

        for row_index, row in enumerate(table.rows[header_row_idx + 1:], start=header_row_idx + 1):
            cells = [c.text.strip() for c in row.cells]
            if not any(cells):
                continue
            parts = [f"{h}: {v}" for h, v in zip(header_cells, cells, strict=False) if v]
            text = "\n".join(parts)
            if text.strip():
                units.append(DocumentUnit(
                    text=text,
                    locator={"table_index": table_index, "row_index": row_index},
                ))

    return units


# Định dạng cũ không được nhận, kèm định dạng nên lưu lại thành. Đọc chúng cần LibreOffice,
# mà ảnh backend không cài: chúng từng được nhận lúc upload rồi hỏng trong job nền, và giảng
# viên chỉ biết khi vào trang theo dõi lỗi. Mọi phần mềm soạn thảo hiện nay đều lưu được
# sang định dạng mới, nên từ chối kèm cách sửa rẻ hơn nhiều so với mang LibreOffice vào ảnh.
LEGACY_EXTENSIONS = {".ppt": ".pptx", ".odp": ".pptx", ".doc": ".docx", ".odt": ".docx", ".rtf": ".docx"}
_AUDIO_VIDEO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac",
    ".mp4", ".mkv", ".webm", ".mov", ".avi",
}

# Nguồn duy nhất cho danh sách định dạng được hỗ trợ -- dùng ở đây (nhánh
# read_document()) VÀ ở app/api/documents.py (kiểm tra sớm trước khi đọc file
# vào bộ nhớ), tránh 2 danh sách lệch nhau.
SUPPORTED_EXTENSIONS = (
    {".pdf", ".docx", ".pptx", ".html", ".htm", ".srt", ".vtt", ".txt"}
    | _AUDIO_VIDEO_EXTENSIONS
)

_SRT_BLOCK_RE = re.compile(
    r"(?:\d+\s*\n)?(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n(.+?)(?=\n\n|\Z)",
    re.DOTALL,
)


def _srt_timestamp_to_ms(ts: str) -> int:
    ts = ts.replace(",", ".")
    h, m, s = ts.split(":")
    sec, ms = s.split(".")
    return ((int(h) * 3600 + int(m) * 60 + int(sec)) * 1000) + int(ms)


def read_transcript_file(path: str) -> list[DocumentUnit]:
    """
    Doc transcript giang vien da co san (khong can chay ASR) -- .srt/.vtt (phu
    de co timestamp, khop thang vao locator={"start_ms","end_ms"}) hoac .txt
    thuan (khong co timestamp, chia theo doan van).
    """
    ext = path.lower().rsplit(".", 1)[-1]
    with open(path, encoding="utf-8") as f:
        content = f.read()
    # Normalize to LF: both the SRT/VTT block regex and the plain-text
    # paragraph split below match a literal "\n\n" -- a Windows-authored file
    # (SubtitleEdit, Windows caption exporters) uses "\r\n\r\n" between
    # blocks, which never matches that literal sequence.
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    if ext in ("srt", "vtt"):
        units = []
        for match in _SRT_BLOCK_RE.finditer(content):
            start_ms = _srt_timestamp_to_ms(match.group(1))
            end_ms = _srt_timestamp_to_ms(match.group(2))
            text = " ".join(line.strip() for line in match.group(3).splitlines() if line.strip())
            if text:
                units.append(DocumentUnit(text=text, locator={"start_ms": start_ms, "end_ms": end_ms}))
        return units

    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    return [DocumentUnit(text=p, locator={"paragraph_index": i}) for i, p in enumerate(paragraphs)]

# Leave headroom below Groq's 25 MB upload limit (decimal bytes).
_AUDIO_UPLOAD_BYTES = 24_000_000
_RATE_LIMIT_ATTEMPTS = 4


def _transcode_to_mp3(input_path: str, out_dir: str) -> str:
    if input_path.lower().endswith(".mp3") and os.path.getsize(input_path) <= _AUDIO_UPLOAD_BYTES:
        return input_path
    out_path = os.path.join(out_dir, "audio.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-vn", "-ac", "1", "-ar", "16000",
         "-acodec", "libmp3lame", "-b:a", "32k", out_path],
        check=True, timeout=300, capture_output=True,
    )
    return out_path


def read_audio_video(path: str) -> list[DocumentUnit]:
    """Compress audio/video for Whisper and retain original-timeline citations."""
    units = []
    with tempfile.TemporaryDirectory() as tmp:
        mp3_path = _transcode_to_mp3(path, tmp)
        pieces = [(mp3_path, 0.0)]
        if os.path.getsize(mp3_path) > _AUDIO_UPLOAD_BYTES:
            manifest = os.path.join(tmp, "pieces.csv")
            # 90 minutes at 32 kbit/s is ~21.6 MB. Copy the already compressed audio;
            # the manifest records actual packet boundaries, not rounded split times.
            subprocess.run(
                ["ffmpeg", "-y", "-i", mp3_path, "-c", "copy", "-f", "segment",
                 "-segment_time", "5400", "-reset_timestamps", "1",
                 "-segment_list", manifest, os.path.join(tmp, "part%03d.mp3")],
                check=True, timeout=300, capture_output=True,
            )
            with open(manifest, newline="", encoding="utf-8") as f:
                pieces = [(os.path.join(tmp, name), float(start)) for name, start, _ in csv.reader(f)]
        for piece, offset in pieces:
            if os.path.getsize(piece) > _AUDIO_UPLOAD_BYTES:
                raise ValueError("Audio vuot gioi han 24 MB sau khi chia")
            for attempt in range(_RATE_LIMIT_ATTEMPTS):
                try:
                    segments = transcribe_audio(piece)
                    break
                except RateLimitError as exc:
                    if attempt == _RATE_LIMIT_ATTEMPTS - 1:
                        raise ValueError("Da het han muc audio Groq Whisper, vui long thu lai sau") from exc
                    try:
                        wait = float(exc.response.headers.get("retry-after", 60))
                    except ValueError:
                        wait = 60
                    time.sleep(min(wait, 3600))
            for segment in segments:
                text = segment["text"].strip()
                if text:
                    units.append(DocumentUnit(text=text, locator={
                        "start_ms": round((offset + segment["start"]) * 1000),
                        "end_ms": round((offset + segment["end"]) * 1000),
                    }))
    if not units:
        raise ValueError("Whisper tra ve transcript rong -- audio co the khong co loi noi")
    return units


def read_document(path: str, *, course_id: str, source_id: str | None = None) -> list[DocumentUnit]:
    if source_id is None:
        source_id = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        units = read_pdf(path)
    elif ext == ".docx":
        units = read_docx(path)
    elif ext == ".pptx":
        units = read_pptx(path)
    elif ext in (".html", ".htm"):
        units = read_html(path)
    elif ext in (".srt", ".vtt", ".txt"):
        units = read_transcript_file(path)
    elif ext in _AUDIO_VIDEO_EXTENSIONS:
        units = read_audio_video(path)
    else:
        raise ValueError(f"Dinh dang khong ho tro: {ext}")

    for u in units:
        # Postgres text columns reject NUL bytes outright (DataError, not a
        # silent strip) -- pymupdf occasionally yields one from certain PDF
        # font/encoding quirks (found via real upload: "Chapter 5_CPU
        # Scheduling.pdf" crashed the whole upload transaction on this,
        # losing the chunks already inserted alongside it). Strip at this
        # single choke point so it's covered regardless of which reader
        # produced the text.
        u.text = u.text.replace("\x00", "")
        u.course_id = course_id
        u.source_id = source_id
    return units
