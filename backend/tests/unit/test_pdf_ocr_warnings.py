"""OCR errors must survive extraction without becoming retrieval text."""

import logging

import pymupdf
import pytest

from app.ingestion.chunker import chunk_units
from app.ingestion.document_readers import DocumentUnit, read_pdf


def _pdf(tmp_path, texts):
    path = tmp_path / "slides.pdf"
    with pymupdf.open() as doc:
        for text in texts:
            page = doc.new_page()
            if text:
                page.insert_text((50, 80), text)
        doc.save(path)
    return str(path)


@pytest.mark.parametrize("failure", [RuntimeError("tesseract unavailable"), "   \n"])
def test_failed_ocr_records_the_page_and_logs_it_without_changing_valid_chunks(
    tmp_path, monkeypatch, caplog, failure,
):
    def ocr(img, lang):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr("app.ingestion.document_readers.pytesseract.image_to_string", ocr)
    with caplog.at_level(logging.WARNING):
        units = read_pdf(_pdf(tmp_path, ["The scheduler selects a process from the ready queue.", ""]))

    assert [u.locator["page"] for u in units] == [1, 2]
    failed = units[1]
    assert failed.text == ""
    assert failed.locator["extraction_method"] == "ocr_failed"
    assert failed.warnings[0]["page"] == 2
    assert failed.warnings[0]["code"] == "ocr_failed"
    assert "2" in failed.warnings[0]["message"]
    assert "page 2" in caplog.text
    chunks = chunk_units(units)
    assert len(chunks) == 1
    assert chunks[0].locator["page"] == 1
    assert "OCR" not in chunks[0].content


def test_failed_ocr_keeps_the_small_existing_text_layer(tmp_path, monkeypatch):
    def ocr(img, lang):
        raise RuntimeError("tesseract unavailable")

    monkeypatch.setattr("app.ingestion.document_readers.pytesseract.image_to_string", ocr)
    units = read_pdf(_pdf(tmp_path, ["Short title"]))

    assert units[0].text == "Short title"
    assert units[0].warnings[0]["page"] == 1


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
    # Blank pages used to be omitted from the deck-title frequency denominator.
    # Recording their warnings must not turn a repeated deck title into a slide heading.
    monkeypatch.setattr("app.ingestion.document_readers.pytesseract.image_to_string", lambda img, lang: "")
    monkeypatch.setattr("app.ingestion.document_readers.title_candidates", lambda page:
                        ["Repeated deck title", f"Slide {page.number + 1}"] if page.number < 3 else [])
    units = read_pdf(_pdf(tmp_path, ["A complete page of teaching content."] * 4 + [""] * 12))
    assert [u.locator["heading"] for u in units[:3]] == ["Slide 1", "Slide 2", "Slide 3"]
