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






# --- OCR và bảng --------------------------------------------------------------


def _pdf(tmp_path, pages):
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((50, 80), text, fontname="helv", fontsize=12)
        else:
            page.draw_rect(pymupdf.Rect(40, 40, 200, 200))  # trang quét: có hình, không có lớp chữ
    path = tmp_path / "scan.pdf"
    doc.save(path)
    doc.close()
    return str(path)


def test_a_page_without_a_text_layer_is_read_by_ocr(tmp_path, monkeypatch):
    from app.ingestion import document_readers

    monkeypatch.setattr(document_readers.pytesseract, "image_to_string", lambda img, lang: "Noi dung trang quet")

    units = document_readers.read_pdf(_pdf(tmp_path, ["A page that has plenty of real text on it.", ""]))

    scanned = [u for u in units if u.locator["page"] == 2]
    assert scanned and scanned[0].text == "Noi dung trang quet"
    assert scanned[0].locator["extraction_method"] == "ocr"


def test_an_ocr_failure_on_one_page_keeps_the_text_of_the_other_pages(tmp_path, monkeypatch):
    from app.ingestion import document_readers

    def broken(img, lang):
        raise RuntimeError("tesseract is not installed")

    monkeypatch.setattr(document_readers.pytesseract, "image_to_string", broken)

    units = document_readers.read_pdf(_pdf(tmp_path, ["A page that has plenty of real text on it.", ""]))

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
