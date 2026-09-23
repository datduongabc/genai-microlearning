from docx import Document as DocxDocument

from app.ingestion.document_readers import read_docx, read_html, read_transcript_file


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


def test_read_transcript_file_srt_parses_timestamps(tmp_path):
    srt = (
        "1\n00:00:00,000 --> 00:00:02,500\nHello world.\n\n"
        "2\n00:00:02,500 --> 00:00:05,000\nSecond caption.\n\n"
    )
    path = tmp_path / "sample.srt"
    path.write_text(srt, encoding="utf-8")

    units = read_transcript_file(str(path))

    assert len(units) == 2
    assert units[0].locator == {"start_ms": 0, "end_ms": 2500}
    assert units[0].text == "Hello world."
    assert units[1].locator == {"start_ms": 2500, "end_ms": 5000}


def test_read_transcript_file_srt_handles_crlf(tmp_path):
    # Windows caption exporters use \r\n\r\n between blocks -- real bug found
    # live: this must not collapse the whole file into one giant segment.
    srt = (
        "1\r\n00:00:00,000 --> 00:00:01,000\r\nFirst.\r\n\r\n"
        "2\r\n00:00:01,000 --> 00:00:02,000\r\nSecond.\r\n\r\n"
    )
    path = tmp_path / "sample_crlf.srt"
    path.write_bytes(srt.encode("utf-8"))

    units = read_transcript_file(str(path))

    assert len(units) == 2


def test_read_transcript_file_txt_splits_by_paragraph(tmp_path):
    txt = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    path = tmp_path / "sample.txt"
    path.write_text(txt, encoding="utf-8")

    units = read_transcript_file(str(path))

    assert len(units) == 3
    assert units[0].locator == {"paragraph_index": 0}


def test_read_html_records_the_heading_level_of_each_heading_unit(tmp_path):
    html = "<html><body><h1>Chapter</h1><p>a</p><h2>Section</h2><p>b</p><h3>Sub</h3><p>c</p></body></html>"
    path = tmp_path / "book.html"
    path.write_text(html, encoding="utf-8")

    units = read_html(str(path))

    assert [(u.locator["heading"], u.locator["heading_level"]) for u in units] == [
        ("Chapter", 1),
        ("Section", 2),
        ("Sub", 3),
    ]


def test_read_docx_records_the_heading_level_of_each_heading_unit(tmp_path):
    doc = DocxDocument()
    doc.add_heading("Chapter", level=1)
    doc.add_paragraph("a")
    doc.add_heading("Section", level=2)
    doc.add_paragraph("b")
    path = tmp_path / "book.docx"
    doc.save(str(path))

    units = read_docx(str(path))

    assert [(u.locator["heading"], u.locator["heading_level"]) for u in units] == [
        ("Chapter", 1),
        ("Section", 2),
    ]
