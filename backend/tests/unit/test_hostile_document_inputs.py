"""A small upload must not make the worker hold gigabytes, and odd bytes in a valid file must not
fail the document as a system fault."""

import zipfile

import pymupdf
import pytest
from pptx import Presentation

from app.ingestion import document_readers
from app.ingestion.document_readers import InputLimitError, preflight_limits


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
    monkeypatch.setattr(document_readers.pytesseract, "image_to_string",
                        lambda img, lang: rendered.append(img.size) or "")
    document_readers.read_pdf(str(path))
    (width, height), = rendered
    assert width * height <= document_readers._MAX_RENDER_PIXELS

