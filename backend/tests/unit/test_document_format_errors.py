import pytest

from app.ingestion.document_readers import read_document


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
