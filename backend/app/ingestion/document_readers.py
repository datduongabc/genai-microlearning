import contextvars
import hashlib
import io
import logging
import math
import os
import re
import subprocess
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any
from zipfile import BadZipFile, ZipFile

import openai
import pymupdf
import pytesseract
from bs4 import BeautifulSoup
from docx import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError as DocxPackageNotFoundError
from litellm.exceptions import (
    APIConnectionError,
    BadGatewayError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from lxml.etree import XMLSyntaxError
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.exc import PackageNotFoundError as PptxPackageNotFoundError
from pptx.oxml import parse_xml
from pptx.shapes.picture import Picture

from app.config import settings
from app.ingestion.figures import MAX_RENDER_PIXELS, is_content_figure, is_page_scan, new_text, ocr_figures
from app.ingestion.formula_repair import repair_page
from app.ingestion.pdf_layout import map_font_text, page_lines, suspect_formula_lines, title_candidates
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
# A numbered section heading: "2.3 Lexical rules", "1 Khái niệm dãy số". Every line that merely
# starts with a number used to count, and on lecture PDFs that is mostly something else: the page
# counter "4 / 33" of a handout, code and data ("100 INT_LIT", "0 => { 0 }"), and the items of a
# numbered list ("1 Nếu α > 1 thì I = ..."). Each one cut a chunk and gave it that label.
_SECTION_NUMBER_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(?=\S)")
_NOT_A_TITLE_RE = re.compile(r"[=<>≤≥⩽⩾∫∑→⇒∼:;,()\[\]{}|/]")
MAX_HEADING_WORDS = 8


def is_numbered_heading(line: str) -> bool:
    """A section number, then a short title: one that starts with a capital letter, holds no formula
    or sentence punctuation, and does not end like a sentence. A list item is a sentence."""
    match = _SECTION_NUMBER_RE.match(line)
    if not match:
        return False
    title = line[match.end():].strip()
    return (
        title[:1].isupper()
        and len(title.split()) <= MAX_HEADING_WORDS
        and not _NOT_A_TITLE_RE.search(title)
        and not title.endswith(".")
    )


def _render_dpi(rect: pymupdf.Rect, dpi: int) -> int:
    area = rect.width * rect.height
    return dpi if area <= 0 else max(1, min(dpi, int(72 * math.sqrt(MAX_RENDER_PIXELS / area))))


def _figure_pages(doc: pymupdf.Document) -> dict[str, int]:
    """Số trang có mặt mỗi ảnh, theo mã băm nội dung ảnh."""
    pages: dict[str, set[int]] = {}
    for page_index, page in enumerate(doc):
        for info in page.get_image_info(hashes=True):
            pages.setdefault(info["digest"], set()).add(page_index)
    return {digest: len(found) for digest, found in pages.items()}


def _page_figures(page: pymupdf.Page, figure_pages: dict[str, int], page_text: str) -> list[bytes]:
    """Ảnh các hình nội dung của trang, vẽ lại đúng vùng của hình: ảnh gốc trong tệp có thể bị
    lật, cắt hay có mặt nạ trong suốt, còn vùng vẽ ra là đúng thứ người đọc thấy."""
    area = page.rect.width * page.rect.height
    images = []
    for info in page.get_image_info(hashes=True):
        box = pymupdf.Rect(info["bbox"]) & page.rect
        ratio = box.width * box.height / area
        if (not box.is_empty and is_content_figure(ratio, figure_pages[info["digest"]])
                and not is_page_scan(ratio, page_text)):
            images.append(page.get_pixmap(clip=box, dpi=_render_dpi(box, 200)).tobytes("png"))
    return images


_FORMULA_WORKERS = 4


def _repair_formulas(repairs: list[tuple[dict, bytes | None]]) -> None:
    """Gửi các trang còn công thức vỡ có ảnh cho model đọc ảnh; trang nào không có bản chép đáng
    tin thì giữ bản dựng lại kèm cảnh báo, để giảng viên biết trang nào cần soát."""
    chosen = [(page, png) for page, png in repairs if png is not None]
    results: list[str | None] = []
    if chosen:
        with ThreadPoolExecutor(max_workers=_FORMULA_WORKERS) as pool:
            # Each call in a copy of the job's context: a 429 then waits as it does for the job, and
            # the call joins the job's record of served models and timings.
            futures = [pool.submit(contextvars.copy_context().run, repair_page, png, page["text"])
                       for page, png in chosen]
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
        repairs: list[tuple[dict, bytes | None]] = []
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
                    pix = page.get_pixmap(dpi=_render_dpi(page.rect, 200))
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
                # Chỉ vẽ trang sẽ thật sự gửi cho model; các trang còn lại chỉ nhận cảnh báo.
                sent = settings.formula_vision_model and len(repairs) < settings.formula_vision_max_pages
                repairs.append((record, page.get_pixmap(dpi=_render_dpi(page.rect, 150)).tobytes("png") if sent else None))
            for text in ocr_figures(_page_figures(page, figure_pages, record["text"])):
                if text and new_text(text, "\n".join([record["text"], *record["figures"]])):
                    record["figures"].append(text)
    finally:
        doc.close()

    _repair_formulas(repairs)

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
            if line_index not in record["code_lines"] and is_numbered_heading(line):
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
_DRAWING_RUN = "{http://schemas.openxmlformats.org/drawingml/2006/main}r"
_RUN_FONTS = tuple(f"{_DRAWING_RUN}Pr/{_DRAWING_RUN[:-1]}{tag}" for tag in ("sym", "latin"))


def _map_symbol_runs(element) -> None:
    """Chữ gõ bằng phông ký hiệu (Wingdings, Symbol) thành Unicode, sửa ngay trên bản đã mở trong bộ nhớ.
    Mã của nó là mã riêng chỉ phông đó vẽ được, nên đọc thẳng ra chỉ là ô trống; tệp gốc không bị ghi lại."""
    for run in element.iter(_DRAWING_RUN):
        text = run.find(_DRAWING_TEXT)
        if text is None or not text.text or not any(0xE000 <= ord(c) <= 0xF8FF for c in text.text):
            continue
        font = next((f.get("typeface") for path in _RUN_FONTS if (f := run.find(path)) is not None), None)
        if font:
            text.text = map_font_text(text.text, font)[0]


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
    _map_symbol_runs(data)
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
    pictures: list[tuple[str, float, bytes]] = []
    slides = []
    for slide_index, slide in enumerate(prs.slides):
        _map_symbol_runs(slide._element)
        if slide.has_notes_slide:
            _map_symbol_runs(slide.notes_slide._element)
        title_shape = slide.shapes.title
        # Chữ của slide, hoặc chỉ số của một hình: hình chỉ được chọn đọc khi đã biết nó có mặt trên
        # bao nhiêu slide, và được đọc cùng lúc cho cả bộ slide.
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
                if blob is not None:
                    digest = hashlib.sha1(blob).hexdigest()
                    picture_slides.setdefault(digest, set()).add(slide_index)
                    area = (shape.width or 0) * (shape.height or 0) / slide_area if slide_area else 0
                    body.append(len(pictures))
                    pictures.append((digest, area, blob))
            else:
                body.extend(_smartart_lines(shape, slide))
        notes = ""
        if slide.has_notes_slide:
            notes = _clean_pptx_text(slide.notes_slide.notes_text_frame.text)
        slides.append((slide_index, title, body, notes))

    chosen = [i for i, (digest, area, _) in enumerate(pictures) if is_content_figure(area, len(picture_slides[digest]))]
    figure_texts = dict(zip(chosen, ocr_figures([pictures[i][2] for i in chosen]), strict=True))
    units = []
    for slide_index, title, body, notes in slides:
        written = "\n".join([title, notes, *(part for part in body if isinstance(part, str))])
        parts = [title] if title else []
        for part in body:
            if isinstance(part, str):
                parts.append(part)
            elif figure_texts.get(part) and new_text(figure_texts[part], written):
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
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".htm"} | _AUDIO_VIDEO_EXTENSIONS

# Giới hạn đầu vào đã chốt: tài liệu tối đa 150 trang/slide, audio/video tối đa 90 phút.
MAX_DOCUMENT_PAGES = 150
# python-docx and python-pptx read every zip member into memory on open, so the unpacked size,
# not the 25 MB upload, is what the worker has to hold.
MAX_UNPACKED_BYTES = 200 * 1024 * 1024


class InputLimitError(ValueError):
    """Tệp vượt giới hạn trang hoặc thời lượng; API trả 413 thay vì để job nền xử lý."""


# Leave headroom below Groq's 25 MB upload limit (decimal bytes).
_AUDIO_UPLOAD_BYTES = 24_000_000
_RATE_LIMIT_WAIT_SECONDS = 600
_MAX_AUDIO_SECONDS = 5400
# MP3 encoder padding makes an exact 90:00 cut probe slightly above 5400 s.
_DURATION_TOLERANCE_SECONDS = 1
_AUDIO_SEGMENT_SECONDS = 600
_AUDIO_OVERLAP_SECONDS = 2
# Shared across document jobs: four uploads per process, not four per document.
_ASR_EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _audio_windows(duration: float) -> list[tuple[float, float]]:
    return [
        (max(0, start - _AUDIO_OVERLAP_SECONDS),
         min(duration, start + _AUDIO_SEGMENT_SECONDS) - max(0, start - _AUDIO_OVERLAP_SECONDS))
        for start in range(0, math.ceil(duration), _AUDIO_SEGMENT_SECONDS)
    ]


def _transcode_to_mp3(input_path: str, out_dir: str, *, start: float = 0,
                      duration: float, index: int = 0) -> str:
    out_path = os.path.join(out_dir, f"part{index:03d}.mp3")
    # Input seeking followed by re-encoding is sample-accurate; stream-copy seeking
    # rounds to packet boundaries and would shift citations at each split.
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(duration),
         "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-acodec", "libmp3lame",
         "-b:a", "32k", "-threads", "1", out_path],
        check=True, timeout=300, capture_output=True,
    )
    return out_path


def _duration_seconds(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=duration", "-of", "csv=p=0", path],
        check=True, timeout=60, capture_output=True, text=True,
    )
    if result.stdout.strip() == "N/A":
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            check=True, timeout=60, capture_output=True, text=True,
        )
    return float(result.stdout)


def _check_duration(duration: float) -> None:
    if duration > _MAX_AUDIO_SECONDS + _DURATION_TOLERANCE_SECONDS:
        raise InputLimitError("Audio/video dài hơn 90 phút; hãy cắt thành các phần tối đa 90 phút.")


def preflight_limits(path: str) -> None:
    """Đếm trang/slide hoặc đo thời lượng trước khi tạo job, để từ chối ngay lúc upload.

    Chỉ ném InputLimitError. Tệp hỏng hay không đọc được thì bỏ qua: reader trong job
    nền vẫn báo lỗi cụ thể hơn cho trường hợp đó.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".docx", ".pptx"):
        try:
            # Reads only the central directory; zipfile stops at the declared size, so it cannot lie.
            with ZipFile(path) as archive:
                unpacked = sum(info.file_size for info in archive.infolist())
        except BadZipFile:
            unpacked = 0
        if unpacked > MAX_UNPACKED_BYTES:
            raise InputLimitError(
                f"Tệp giải nén ra {unpacked // (1024 * 1024)} MB, vượt giới hạn "
                f"{MAX_UNPACKED_BYTES // (1024 * 1024)} MB; hãy nén lại ảnh hoặc tách tệp."
            )
    try:
        if ext == ".pdf":
            with pymupdf.open(path) as doc:
                pages = doc.page_count
        elif ext == ".pptx":
            pages = len(Presentation(path).slides)
        elif ext in _AUDIO_VIDEO_EXTENSIONS:
            duration = _duration_seconds(path)
            pages = 0
        else:
            return
    except Exception:
        return
    if ext in _AUDIO_VIDEO_EXTENSIONS:
        _check_duration(duration)
    elif pages > MAX_DOCUMENT_PAGES:
        unit = "slide" if ext == ".pptx" else "trang"
        raise InputLimitError(
            f"Tài liệu có {pages} {unit}, vượt giới hạn {MAX_DOCUMENT_PAGES} {unit}. "
            "Hãy tách thành nhiều tệp nhỏ hơn."
        )


def _transcribe_piece(path: str, offset: float, deadline: float,
                      failed: threading.Event) -> list[dict]:
    for attempt in range(3):
        # A sibling already failed the document; never spend ASR quota on it.
        if failed.is_set():
            return []
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError("Audio transcription deadline expired")
            return transcribe_audio(path)
        except Exception as exc:
            rate_limited = isinstance(exc, (RateLimitError, openai.RateLimitError))
            transient = rate_limited or isinstance(exc, (
                APIConnectionError, Timeout, InternalServerError, BadGatewayError, ServiceUnavailableError,
                openai.APIConnectionError, openai.InternalServerError,
            )) or (isinstance(exc, openai.APIStatusError) and exc.status_code == 408)
            headers = getattr(getattr(exc, "response", None), "headers", {})
            try:
                delay = float(headers.get("retry-after", 60 if rate_limited else 2 ** attempt))
                if not math.isfinite(delay):
                    raise ValueError("invalid retry-after")
                delay = max(1, delay)
            except (TypeError, ValueError):
                delay = 60 if rate_limited else 2 ** attempt
            if transient and not rate_limited and attempt == 2 and time.monotonic() < deadline:
                # Preserve transient failures so the durable job queue can retry later.
                raise
            if not transient or attempt == 2 or time.monotonic() + delay >= deadline:
                minutes, seconds = divmod(int(offset), 60)
                reason = "Đã hết hạn mức chuyển âm thanh thành chữ" if rate_limited else type(exc).__name__
                # ValueError is terminal and is shown in the document's job monitor.
                # Never return a successful but incomplete document.
                raise ValueError(
                    f"Không chuyển được đoạn {minutes:02d}:{seconds:02d} thành chữ ({reason}). "
                    "Tài liệu chưa được nhập, hãy thử lại sau."
                ) from exc
            failed.wait(delay)
    raise AssertionError("unreachable")


def _merge_audio_segments(pieces: list[tuple[float, float, list[dict]]]) -> list[DocumentUnit]:
    units: list[DocumentUnit] = []
    for offset, duration, segments in pieces:
        incoming = []
        for segment in segments:
            text = segment["text"].strip()
            start = max(0, min(duration, float(segment["start"])))
            end = max(start, min(duration, float(segment["end"])))
            if text and end > start:
                incoming.append(DocumentUnit(text=text, locator={
                    "start_ms": round((offset + start) * 1000),
                    "end_ms": round((offset + end) * 1000),
                }))
        # Only compare text within the shared audio window. Repetition elsewhere
        # in a lecture is meaningful and must survive.
        overlap_end = round((offset + _AUDIO_OVERLAP_SECONDS) * 1000)
        previous = " ".join(u.text for u in units if u.locator["end_ms"] > offset * 1000)
        prefix = " ".join(u.text for u in incoming if u.locator["start_ms"] < overlap_end)
        normalize = lambda text: [re.sub(r"\W", "", word).casefold() for word in text.split()]
        old_words, new_words = normalize(previous), normalize(prefix)
        # A final short utterance ("Yeah") or a truncated word can follow the
        # shared phrase, so a multi-word match may end up to two words early.
        # ponytail: exact words only; word-level alignment if paraphrased overlaps matter.
        duplicate = next((n for n in range(min(len(old_words), len(new_words)), 1, -1)
                          if old_words[-n:] == new_words[:n] or (n >= 2 and any(
                              old_words[i:i + n] == new_words[:n]
                              for i in range(max(0, len(old_words) - n - 2), len(old_words) - n + 1)))), 0)
        previous_end = max((u.locator["end_ms"] for u in units), default=0)
        for unit in incoming:
            words = unit.text.split()
            trim = min(duplicate, len(words))
            duplicate -= trim
            if trim:
                unit.text = " ".join(words[trim:])
                unit.locator["start_ms"] = min(unit.locator["end_ms"],
                                                max(unit.locator["start_ms"], previous_end))
            if unit.text:
                units.append(unit)
    return sorted(units, key=lambda unit: unit.locator["start_ms"])


def _transcribe_window(path: str, tmp: str, index: int, start: float,
                       length: float, deadline: float, failed: threading.Event) -> list[dict]:
    if failed.is_set():
        return []
    try:
        try:
            piece = _transcode_to_mp3(path, tmp, start=start, duration=length, index=index)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            minutes, seconds = divmod(int(start), 60)
            raise ValueError(f"Không xử lý được đoạn {minutes:02d}:{seconds:02d} của tệp âm thanh") from exc
        if os.path.getsize(piece) > _AUDIO_UPLOAD_BYTES:
            raise ValueError("Audio vuot gioi han 24 MB sau khi chia")
        return _transcribe_piece(piece, start, deadline, failed)
    except Exception:
        failed.set()
        raise


def read_audio_video(path: str) -> list[DocumentUnit]:
    """Normalize every input, transcribe bounded parallel windows, retain absolute citations."""
    duration = _duration_seconds(path)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Audio co thoi luong khong hop le")
    _check_duration(duration)
    with tempfile.TemporaryDirectory() as tmp:
        windows = _audio_windows(duration)
        deadline = time.monotonic() + _RATE_LIMIT_WAIT_SECONDS
        failed = threading.Event()
        futures = [_ASR_EXECUTOR.submit(_transcribe_window, path, tmp, index, start, length,
                                        deadline, failed)
                   for index, (start, length) in enumerate(windows)]
        # All readers must finish before the TemporaryDirectory removes their input.
        wait(futures)
        units = _merge_audio_segments([
            (start, length, future.result())
            for (start, length), future in zip(windows, futures, strict=True)
        ])
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
        # produced the text. jsonb rejects it too, and headings come from the same lines.
        u.text = u.text.replace("\x00", "")
        u.locator = {k: v.replace("\x00", "") if isinstance(v, str) else v for k, v in u.locator.items()}
        u.course_id = course_id
        u.source_id = source_id
    return units
