import csv
import hashlib
import io
import logging
import os
import re
import subprocess
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from zipfile import BadZipFile

import pymupdf
import pytesseract
from bs4 import BeautifulSoup
from docx import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError as DocxPackageNotFoundError
from litellm.exceptions import RateLimitError
from lxml.etree import XMLSyntaxError
from openai import RateLimitError as OpenAIRateLimitError
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.exc import PackageNotFoundError as PptxPackageNotFoundError
from pptx.oxml import parse_xml
from pptx.shapes.picture import Picture

from app.config import settings
from app.ingestion.figures import is_content_figure, new_text, ocr_figures
from app.ingestion.formula_repair import repair_page
from app.ingestion.pdf_layout import page_lines, suspect_formula_lines, title_candidates
from app.llm.gateway import transcribe_audio

logger = logging.getLogger(__name__)


@dataclass
class DocumentUnit:
    text: str
    locator: dict[str, Any] = field(default_factory=dict)
    course_id: str = ""
    source_id: str = ""
    warnings: list[dict[str, Any]] = field(default_factory=list)


MIN_TEXT_LEN_PER_PAGE = 20
HEADING_RE = re.compile(r"^\s*(\d+(\.\d+)*)\.?\s+\S")


def _figure_pages(doc: pymupdf.Document) -> dict[str, int]:
    """Số trang có mặt mỗi ảnh, theo mã băm nội dung ảnh."""
    pages: dict[str, set[int]] = {}
    for page_index, page in enumerate(doc):
        for info in page.get_image_info(hashes=True):
            pages.setdefault(info["digest"], set()).add(page_index)
    return {digest: len(found) for digest, found in pages.items()}


def _page_figures(page: pymupdf.Page, figure_pages: dict[str, int]) -> list[bytes]:
    """Ảnh các hình nội dung của trang, vẽ lại đúng vùng của hình: ảnh gốc trong tệp có thể bị
    lật, cắt hay có mặt nạ trong suốt, còn vùng vẽ ra là đúng thứ người đọc thấy."""
    area = page.rect.width * page.rect.height
    images = []
    for info in page.get_image_info(hashes=True):
        box = pymupdf.Rect(info["bbox"]) & page.rect
        if not box.is_empty and is_content_figure(box.width * box.height / area, figure_pages[info["digest"]]):
            images.append(page.get_pixmap(clip=box, dpi=200).tobytes("png"))
    return images


_FORMULA_WORKERS = 4


def _repair_formulas(repairs: list[tuple[dict, bytes]]) -> None:
    """Gửi các trang còn công thức vỡ cho model đọc ảnh; trang nào không có bản chép đáng tin thì
    giữ bản dựng lại kèm cảnh báo, để giảng viên biết trang nào cần soát."""
    chosen = repairs[:settings.formula_vision_max_pages] if settings.formula_vision_model else []
    results: list[str | None] = []
    if chosen:
        with ThreadPoolExecutor(max_workers=_FORMULA_WORKERS) as pool:
            futures = [pool.submit(repair_page, png, page["text"]) for page, png in chosen]
        for (page, _), future in zip(chosen, futures, strict=True):
            try:
                results.append(future.result())
            except Exception as e:
                logger.warning(f"[document_readers] formula re-read failed on page {page['page']}: {e}")
                results.append(None)
    for index, (page, _) in enumerate(repairs):
        text = results[index] if index < len(results) else None
        if text:
            page.update(text=text, code_lines=set(), extraction_method="vision")
        else:
            page["warnings"].append({"code": "formula_unverified", "page": page["page"]})


def read_pdf(path: str) -> list[DocumentUnit]:
    doc = pymupdf.open(path)
    try:
        if doc.needs_pass:
            raise ValueError("PDF được mã hóa; hãy bỏ mật khẩu rồi tải lại tài liệu.")
        figure_pages = _figure_pages(doc)
        page_records: list[dict] = []
        repairs: list[tuple[dict, bytes]] = []
        figures: list[tuple[dict, bytes]] = []
        for page_index, page in enumerate(doc):
            code_lines: set[int] = set()
            math_lines: set[int] = set()
            lines = page_lines(page, code_lines=code_lines, math_lines=math_lines)
            record = {
                "page": page_index + 1, "text": "\n".join(lines), "code_lines": code_lines,
                "titles": title_candidates(page), "extraction_method": "text_layer", "warnings": [], "figures": [],
            }
            page_records.append(record)
            if len(record["text"].strip()) < MIN_TEXT_LEN_PER_PAGE:
                try:
                    pix = page.get_pixmap(dpi=200)
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    ocr_text = pytesseract.image_to_string(img, lang="vie+eng")
                    if not ocr_text.strip():
                        raise ValueError("OCR returned no readable text")
                    record.update(text=ocr_text, code_lines=set(), extraction_method="ocr")
                except Exception as e:
                    # OCR is best-effort per page -- a single bad/scanned page
                    # (or a misconfigured OCR toolchain) must not discard the
                    # text already successfully extracted from every other
                    # page in the document.
                    logger.warning(f"[document_readers] OCR failed on page {page_index + 1} of {path}: {e}")
                    if not record["text"].strip():
                        record["warnings"].append({"code": "ocr_failed", "page": page_index + 1})
                continue
            if suspect_formula_lines([lines[i] for i in sorted(math_lines)]):
                repairs.append((record, page.get_pixmap(dpi=150).tobytes("png")))
            figures.extend((record, image) for image in _page_figures(page, figure_pages))
    finally:
        doc.close()

    _repair_formulas(repairs)
    for (record, _), text in zip(figures, ocr_figures([image for _, image in figures]), strict=True):
        if text and new_text(text, "\n".join([record["text"], *record["figures"]])):
            record["figures"].append(text)

    raw_units = []
    for record in page_records:
        if record["warnings"] and not record["text"].strip():
            raw_units.append({
                "page": record["page"], "lines": [], "numbered": None, "titles": record["titles"],
                "extraction_method": "ocr_failed", "warnings": record["warnings"],
            })
            continue
        # Chữ đọc từ hình đứng cuối trang, sau phần lớp chữ mà hình minh họa.
        lines_all = record["text"].split("\n") + [f"Hình: {text}" for text in record["figures"]]
        current_lines, current_heading = [], None
        warnings = record["warnings"]

        def flush():
            nonlocal warnings
            if current_lines and "".join(current_lines).strip():
                stripped = [line.rstrip() for line in current_lines if line.strip()]
                raw_units.append({
                    "page": record["page"], "lines": stripped, "numbered": current_heading,
                    "titles": record["titles"], "extraction_method": record["extraction_method"],
                    "warnings": warnings,
                })
                # Cảnh báo của trang chỉ ghi một lần, ở đơn vị đầu tiên của trang.
                warnings = []

        for line_index, line in enumerate(lines_all):
            if line_index not in record["code_lines"] and HEADING_RE.match(line):
                flush()
                current_lines, current_heading = [line], line.strip()
            else:
                current_lines.append(line)
        flush()

    total = len(raw_units)
    if total == 0:
        return []
    # Warning-only pages carry metadata, not slide titles or chunk boundaries.
    pages = {u["page"] for u in raw_units if u["lines"]}
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
            warnings=u.get("warnings", []),
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


_DRAWING_PARAGRAPH = "{http://schemas.openxmlformats.org/drawingml/2006/main}p"
_DRAWING_TEXT = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"


def _smartart_lines(shape, slide) -> list[str]:
    """Chữ của một sơ đồ SmartArt. Nó nằm trong phần dữ liệu riêng của sơ đồ chứ không trong slide,
    nên đọc slide thì một slide chỉ có sơ đồ trông như slide trống."""
    ids = shape._element.xpath('.//*[local-name()="relIds"]/@*[local-name()="dm"]')
    if not ids:
        return []
    try:
        data = parse_xml(slide.part.related_part(ids[0]).blob)
    except (KeyError, XMLSyntaxError):
        return []
    lines = []
    for paragraph in data.iter(_DRAWING_PARAGRAPH):
        # Một chữ có dấu có thể bị tách thành hai lượt gõ, chữ gốc và dấu thanh kết hợp.
        text = unicodedata.normalize("NFC", "".join(t.text or "" for t in paragraph.iter(_DRAWING_TEXT)))
        if _clean_pptx_text(text):
            lines.append(_clean_pptx_text(text))
    return lines


def _picture_blob(shape) -> bytes | None:
    try:
        return shape.image.blob
    except (AttributeError, KeyError, ValueError):
        # Ảnh liên kết ra ngoài tệp, không được nhúng: không có gì để đọc.
        return None


def read_pptx(path: str) -> list[DocumentUnit]:
    """One unit per slide with any text: the title, then everything else on the slide in the order
    it was placed (text boxes, tables, shapes inside groups, SmartArt, the text inside pictures),
    then the speaker notes."""
    prs = Presentation(path)
    slide_area = prs.slide_width * prs.slide_height
    picture_slides: dict[str, set[int]] = {}
    for slide_index, slide in enumerate(prs.slides):
        for shape in _iter_pptx_shapes(slide.shapes):
            if isinstance(shape, Picture) and (blob := _picture_blob(shape)) is not None:
                picture_slides.setdefault(hashlib.sha1(blob).hexdigest(), set()).add(slide_index)

    slides = []
    figures: list[bytes] = []
    for slide_index, slide in enumerate(prs.slides):
        title_shape = slide.shapes.title
        # Chữ của slide, hoặc chỉ số của một hình chờ OCR: hình được đọc cùng lúc cho cả bộ slide.
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
            elif isinstance(shape, Picture):
                blob = _picture_blob(shape)
                area = (shape.width or 0) * (shape.height or 0) / slide_area if slide_area else 0
                if blob is not None and is_content_figure(area, len(picture_slides[hashlib.sha1(blob).hexdigest()])):
                    body.append(len(figures))
                    figures.append(blob)
            else:
                body.extend(_smartart_lines(shape, slide))
        notes = ""
        if slide.has_notes_slide:
            notes = _clean_pptx_text(slide.notes_slide.notes_text_frame.text)
        slides.append((slide_index, title, body, notes))

    figure_texts = ocr_figures(figures)
    units = []
    for slide_index, title, body, notes in slides:
        written = "\n".join([title, notes, *(part for part in body if isinstance(part, str))])
        parts = [title] if title else []
        for part in body:
            if isinstance(part, str):
                parts.append(part)
            elif figure_texts[part] and new_text(figure_texts[part], written):
                parts.append(f"Hình: {figure_texts[part]}")
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
    current_lines, current_heading = [], None

    def flush():
        if current_lines and "\n".join(current_lines).strip():
            locator = {"heading": current_heading}
            units.append(DocumentUnit(text="\n".join(current_lines).strip(), locator=locator))

    body = soup.body or soup
    for el in body.find_all([*HEADING_TAGS, "p"]):
        if el.name in HEADING_TAGS:
            flush()
            current_heading = _clean_html_text(el)
            current_lines = [current_heading]
        else:
            text = _clean_html_text(el)
            if text:
                current_lines.append(text)
    flush()
    return units


def read_docx(path: str) -> list[DocumentUnit]:
    doc = DocxDocument(path)
    units: list[DocumentUnit] = []

    current_lines, current_heading = [], None

    def flush_para():
        if current_lines and "\n".join(current_lines).strip():
            locator = {"heading": current_heading}
            units.append(DocumentUnit(text="\n".join(current_lines).strip(), locator=locator))

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if para.style.name.startswith("Heading"):
            flush_para()
            current_lines, current_heading = [text], text
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
# ponytail: total in-job wait stays under the 15-minute stuck-job threshold, and audio
# longer than one hourly Groq window (7,200 audio-seconds) is refused up front, because
# a retry would re-send every finished piece and hit the same limit again.
_RATE_LIMIT_WAIT_SECONDS = 600
_MAX_AUDIO_SECONDS = 7000


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


def _duration_seconds(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        check=True, timeout=60, capture_output=True, text=True,
    )
    return float(result.stdout)


def read_audio_video(path: str) -> list[DocumentUnit]:
    """Compress audio/video for Whisper and retain original-timeline citations."""
    units = []
    waited = 0
    with tempfile.TemporaryDirectory() as tmp:
        mp3_path = _transcode_to_mp3(path, tmp)
        if _duration_seconds(mp3_path) > _MAX_AUDIO_SECONDS:
            raise ValueError(
                "Audio dài hơn khoảng 2 giờ vượt hạn mức phiên âm miễn phí; "
                "hãy cắt file thành các phần dưới 2 giờ rồi tải lên từng phần."
            )
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
            while True:
                try:
                    segments = transcribe_audio(piece)
                    break
                # litellm.transcription raises the OpenAI SDK's class, not litellm's.
                except (RateLimitError, OpenAIRateLimitError) as exc:
                    try:
                        wait = max(float(exc.response.headers.get("retry-after", 60)), 1)
                    except ValueError:
                        wait = 60
                    if waited + wait > _RATE_LIMIT_WAIT_SECONDS:
                        raise ValueError("Da het han muc audio Groq Whisper, vui long thu lai sau") from exc
                    time.sleep(wait)
                    waited += wait
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


def _read_document_units(path: str) -> list[DocumentUnit]:
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
    return units


def read_document(path: str, *, course_id: str, source_id: str | None = None) -> list[DocumentUnit]:
    if source_id is None:
        source_id = os.path.basename(path)
    try:
        units = _read_document_units(path)
    except (pymupdf.FileDataError, BadZipFile, DocxPackageNotFoundError,
            PptxPackageNotFoundError, XMLSyntaxError, UnicodeDecodeError) as error:
        # The queue already treats ValueError as terminal; provider/network errors still retry.
        raise ValueError(str(error)) from error

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
