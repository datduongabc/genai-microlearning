"""Unit tests for document readers, OCR handling/warnings, hostile inputs, and upload limits."""

import logging
import uuid
import zipfile
from unittest.mock import MagicMock

import pymupdf
import pytest
from docx import Document as DocxDocument
from fastapi import HTTPException
from pptx import Presentation
from starlette.requests import Request

from app.api.documents import UploadRequest, request_upload
from app.ingestion import document_readers, figures
from app.ingestion.chunker import chunk_units
from app.ingestion.document_readers import (
    DocumentUnit,
    InputLimitError,
    is_numbered_heading,
    preflight_limits,
    read_document,
    read_docx,
    read_html,
    read_pdf,
)
from app.models import Course, User


def _pdf(tmp_path, pages, *, draw_blank_rect: bool = False):
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((50, 80), text, fontname="helv", fontsize=12)
        elif draw_blank_rect:
            page.draw_rect(pymupdf.Rect(40, 40, 200, 200))
    path = tmp_path / "sample.pdf"
    doc.save(path)
    doc.close()
    return str(path)


def _stub_ocr(monkeypatch, fn):
    monkeypatch.setattr(document_readers.pytesseract, "image_to_string", fn)


def test_read_html_splits_by_heading(tmp_path):
    html = """
    <html><body>
    <h1>Chapter 1</h1>
    <p>First paragraph.</p>
    <h2>Section 1.1</h2>
    <p>Second paragraph.</p>
    </body></html>
    """
    path = tmp_path / "sample.html"
    path.write_text(html, encoding="utf-8")

    units = read_html(str(path))

    assert len(units) == 2
    assert units[0].locator["heading"] == "Chapter 1"
    assert "First paragraph" in units[0].text
    assert units[1].locator["heading"] == "Section 1.1"
    assert "Second paragraph" in units[1].text


def test_a_page_without_a_text_layer_is_read_by_ocr(tmp_path, monkeypatch):
    _stub_ocr(monkeypatch, lambda img, lang: "Noi dung trang quet")

    units = read_pdf(_pdf(tmp_path, ["A page that has plenty of real text on it.", ""], draw_blank_rect=True))

    scanned = [u for u in units if u.locator["page"] == 2]
    assert scanned and scanned[0].text == "Noi dung trang quet"
    assert scanned[0].locator["extraction_method"] == "ocr"


def test_an_ocr_failure_on_one_page_keeps_the_text_of_the_other_pages(tmp_path, monkeypatch):
    def broken(img, lang):
        raise RuntimeError("tesseract is not installed")

    _stub_ocr(monkeypatch, broken)

    units = read_pdf(_pdf(tmp_path, ["A page that has plenty of real text on it.", ""], draw_blank_rect=True))

    assert [u.locator["page"] for u in units if u.text] == [1]
    assert units[1].warnings[0]["page"] == 2
    assert "plenty of real text" in units[0].text


def test_a_docx_table_is_read_row_by_row_under_its_header_skipping_a_merged_banner(tmp_path):
    doc = DocxDocument()
    table = doc.add_table(rows=4, cols=2)
    banner = table.rows[0].cells[0].merge(table.rows[0].cells[1])
    banner.text = "Bảng 1. Độ phức tạp"
    table.rows[1].cells[0].text, table.rows[1].cells[1].text = "Thuật toán", "Độ phức tạp"
    table.rows[2].cells[0].text, table.rows[2].cells[1].text = "Heap sort", "O(n log n)"
    table.rows[3].cells[0].text, table.rows[3].cells[1].text = "", ""
    path = tmp_path / "bang.docx"
    doc.save(str(path))

    rows = [u.text for u in read_docx(str(path)) if "table_index" in u.locator]

    assert rows == ["Thuật toán: Heap sort\nĐộ phức tạp: O(n log n)"]


@pytest.mark.parametrize("extension", ["pdf", "docx", "pptx", "html", "txt"])
def test_corrupt_document_format_is_terminal(extension, tmp_path):
    path = tmp_path / f"broken.{extension}"
    path.write_bytes(b"\xff\x00invalid format")
    with pytest.raises(ValueError):
        read_document(str(path), course_id="course")


def test_empty_pdf_is_terminal(tmp_path):
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    with pytest.raises(ValueError):
        read_document(str(path), course_id="course")


@pytest.mark.parametrize("line", [
    "1 Introduction to Syntax Analysis",
    "2 Context-free grammar",
    "1 Khái niệm dãy số",
    "3 Giới hạn của dãy số",
    "2.3 Lexical rules",
    "1.2. Biểu thức chính quy",
])
def test_section_headings(line):
    assert is_numbered_heading(line)


@pytest.mark.parametrize("line", [
    "4 / 33",
    "33 / 33",
    "100 INT_LIT",
    "65000 words",
    "0 => { 0 }",
    "0 | 1 => {0,1}",
    "9 - 5 - 2 ⇒6 ⇒2",
    "1 0 . 0 e 2 0 . . . Input Tape",
    "1 Nếu α > 1 thì I = ∫_a^(+∞) dx/x^α hội tụ.",
    "1 Nếu f (x), g(x) là những VCB tương đương khi",
    "1. Tính tăng và tính giảm.",
    "2 Replace any non-terminal symbol X in the string with the",
    "1 Theo kết quả điều tra dân số năm 2000 thì",
    "1. Mutual Exclusion – If process P_i is executing in its critical section",
    "630 m. Phương trình của cổng chào là",
    "3 tokens are returned",
])
def test_not_headings(line):
    assert not is_numbered_heading(line)


def _deck_with_extra_member(path, member_bytes: int) -> str:
    Presentation().save(path)
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ppt/unused.bin", b"\0" * member_bytes)
    return str(path)


def test_an_office_file_that_unpacks_past_the_cap_is_refused_before_it_is_opened(tmp_path, monkeypatch):
    monkeypatch.setattr(document_readers, "MAX_UNPACKED_BYTES", 1_000_000)
    preflight_limits(_deck_with_extra_member(tmp_path / "ok.pptx", 10_000))

    def never_opened(path):
        raise AssertionError("the file must be refused before python-pptx unpacks it")

    monkeypatch.setattr(document_readers, "Presentation", never_opened)
    with pytest.raises(InputLimitError, match="giải nén"):
        preflight_limits(_deck_with_extra_member(tmp_path / "bomb.pptx", 2_000_000))
    with pytest.raises(InputLimitError, match="giải nén"):
        preflight_limits(_deck_with_extra_member(tmp_path / "bomb.docx", 2_000_000))


def test_a_corrupt_office_file_is_left_to_the_reader(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a zip")
    preflight_limits(str(path))


def test_a_blank_page_with_a_huge_mediabox_is_rendered_under_the_pixel_cap(tmp_path, monkeypatch):
    path = tmp_path / "huge.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=200 * 72, height=200 * 72)
        doc.save(path)
    rendered = []
    _stub_ocr(monkeypatch, lambda img, lang: rendered.append(img.size) or "")
    read_pdf(str(path))
    (width, height), = rendered
    assert width * height <= figures.MAX_RENDER_PIXELS


def test_a_nul_byte_in_a_pdf_heading_is_stripped_from_the_locator(tmp_path):
    path = tmp_path / "nul.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 60), "1 Gioi\x00thieu He dieu hanh", fontname="helv", fontsize=14)
        page.insert_text((50, 100), "Noi dung cua phan gioi thieu he dieu hanh.", fontname="helv", fontsize=11)
        doc.save(path)
    units = read_document(str(path), course_id="c")
    assert units[0].locator["heading"] == "1 Gioithieu He dieu hanh"
    assert all("\x00" not in u.text for u in units)


@pytest.mark.parametrize("failure", [RuntimeError("tesseract unavailable"), "   \n"])
def test_failed_ocr_records_the_page_and_logs_it_without_changing_valid_chunks(
    tmp_path, monkeypatch, caplog, failure,
):
    def ocr(img, lang):
        if isinstance(failure, Exception):
            raise failure
        return failure

    _stub_ocr(monkeypatch, ocr)
    with caplog.at_level(logging.WARNING):
        units = read_pdf(_pdf(tmp_path, ["The scheduler selects a process from the ready queue.", ""]))

    assert [u.locator["page"] for u in units] == [1, 2]
    failed = units[1]
    assert failed.text == ""
    assert failed.locator["extraction_method"] == "ocr_failed"
    assert failed.warnings == [{"code": "ocr_failed", "page": 2}]
    assert units[0].warnings == []
    assert "page 2" in caplog.text
    chunks = chunk_units(units)
    assert len(chunks) == 1
    assert chunks[0].locator["page"] == 1
    assert "OCR" not in chunks[0].content


def test_failed_ocr_keeps_the_small_existing_text_layer(tmp_path, monkeypatch):
    def ocr(img, lang):
        raise RuntimeError("tesseract unavailable")

    _stub_ocr(monkeypatch, ocr)
    units = read_pdf(_pdf(tmp_path, ["Short title"]))

    assert units[0].text == "Short title"
    assert units[0].locator["extraction_method"] == "text_layer"
    assert units[0].warnings == []


def test_an_empty_warning_page_does_not_enter_a_chunk_locator():
    units = [
        DocumentUnit("First page of teaching content", {"page": 1}),
        DocumentUnit("", {"page": 2, "extraction_method": "ocr_failed"}),
        DocumentUnit("Third page of teaching content", {"page": 3}),
    ]
    chunks = chunk_units(units)
    assert len(chunks) == 1
    assert [loc["page"] for loc in chunks[0].locator["spans"]] == [1, 3]


def test_warning_only_pages_do_not_change_existing_slide_headings(tmp_path, monkeypatch):
    _stub_ocr(monkeypatch, lambda img, lang: "")
    monkeypatch.setattr(
        document_readers,
        "title_candidates",
        lambda page: ["Repeated deck title", f"Slide {page.number + 1}"] if page.number < 3 else [],
    )
    units = read_pdf(_pdf(tmp_path, ["A complete page of teaching content."] * 4 + [""] * 12))
    assert [u.locator["heading"] for u in units[:3]] == ["Slide 1", "Slide 2", "Slide 3"]


def test_ocr_uses_vietnamese_first_at_200_dpi(tmp_path, monkeypatch):
    calls = []

    def ocr(img, lang):
        calls.append((img.size, lang))
        return "Readable OCR text"

    _stub_ocr(monkeypatch, ocr)
    units = read_pdf(_pdf(tmp_path, [""]))
    assert calls == [((1653, 2339), "vie+eng")]
    assert units[0].text == "Readable OCR text"
    assert units[0].warnings == []


MB = 1024 * 1024


@pytest.mark.parametrize("filename,limit_mb,message", [
    ("lecture.mp3", 200, "Tệp âm thanh tối đa 200 MB"),
    ("lecture.mp4", 1024, "Tệp video tối đa 1 GB"),
])
def test_media_upload_rejects_above_its_limit(filename, limit_mb, message):
    user = User(id=uuid.uuid4())
    course = Course(id=uuid.uuid4(), owner_id=user.id)
    db = MagicMock()
    db.get.return_value = course
    db.query.return_value.filter.return_value.first.return_value = None
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})

    with pytest.raises(HTTPException) as error:
        request_upload(
            request=request,
            course_id=course.id,
            body=UploadRequest(filename=filename, size=limit_mb * MB + 1),
            db=db,
            current_user=user,
        )

    assert error.value.status_code == 413
    assert error.value.detail == message
    db.add.assert_not_called()
    db.commit.assert_not_called()
