import pymupdf
from docx import Document as DocxDocument

from app.ingestion.document_readers import read_docx, read_html
from app.ingestion.outline import OutlineEntry, clean_outline, extract_outline

BODY_SIZE = 11


def _make_pdf(path, pages, toc=None):
    """pages: a list of pages, each a list of (text, font size) lines."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        y = 60
        for text, size in lines:
            page.insert_text((60, y), text, fontsize=size)
            y += size + 6
    if toc:
        doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


def _body(count=10):
    return [(f"Body sentence number {i} with plain ordinary words in it.", BODY_SIZE) for i in range(count)]


def test_pdf_bookmarks_become_the_outline(tmp_path):
    path = tmp_path / "book.pdf"
    _make_pdf(
        path,
        [_body(), _body(), _body()],
        toc=[[1, "Chapter 1", 1], [2, "Section 1.1", 2], [1, "Chapter 2", 3]],
    )

    assert extract_outline(str(path), []) == [
        OutlineEntry(1, "Chapter 1", 1),
        OutlineEntry(2, "Section 1.1", 2),
        OutlineEntry(1, "Chapter 2", 3),
    ]


def test_pdf_without_bookmarks_uses_heading_font_sizes(tmp_path):
    path = tmp_path / "book.pdf"
    _make_pdf(
        path,
        [
            [("Chapter One", 22), *_body(6), ("Section A", 16), *_body(6)],
            [("Chapter Two", 22), *_body(8)],
        ],
    )

    assert extract_outline(str(path), []) == [
        OutlineEntry(1, "Chapter One", 1),
        OutlineEntry(2, "Section A", 1),
        OutlineEntry(1, "Chapter Two", 2),
    ]


def test_running_headers_are_not_treated_as_headings(tmp_path):
    header = ("Course Notes Running Header", 16)
    path = tmp_path / "book.pdf"
    _make_pdf(
        path,
        [
            [header, ("Chapter One", 22), *_body()],
            [header, *_body()],
            [header, ("Chapter Two", 22), *_body()],
            [header, *_body()],
            [header, *_body()],
        ],
    )

    assert [e.title for e in extract_outline(str(path), [])] == ["Chapter One", "Chapter Two"]


def test_a_heading_wrapped_over_two_lines_is_one_entry(tmp_path):
    path = tmp_path / "book.pdf"
    _make_pdf(
        path,
        [
            [("Chapter One:", 22), ("Introduction to Sorting", 22), *_body()],
            [("Chapter Two", 22), *_body()],
        ],
    )

    assert [e.title for e in extract_outline(str(path), [])] == [
        "Chapter One: Introduction to Sorting",
        "Chapter Two",
    ]


def test_a_chapter_title_and_the_section_right_under_it_stay_separate(tmp_path):
    path = tmp_path / "book.pdf"
    _make_pdf(
        path,
        [
            [("Chapter One", 22), ("Section A", 16), *_body()],
            [("Chapter Two", 22), *_body()],
        ],
    )

    assert extract_outline(str(path), []) == [
        OutlineEntry(1, "Chapter One", 1),
        OutlineEntry(2, "Section A", 1),
        OutlineEntry(1, "Chapter Two", 2),
    ]


def test_wrapped_heading_lines_are_not_merged_past_the_length_limit(tmp_path):
    long_line = "word " * 9
    path = tmp_path / "book.pdf"
    _make_pdf(
        path,
        [
            [("Chapter One", 22), (long_line, 22), (long_line, 22), (long_line, 22), *_body()],
            [("Chapter Two", 22), *_body()],
        ],
    )

    titles = [e.title for e in extract_outline(str(path), [])]

    assert len(titles) == 3
    assert titles[0].startswith("Chapter One word")
    assert all(len(t) <= 120 for t in titles)
    assert titles[-1] == "Chapter Two"


def test_a_running_header_beside_a_heading_of_the_same_size_is_not_glued_to_it(tmp_path):
    header = ("Course Notes Running Header", 22)
    path = tmp_path / "book.pdf"
    _make_pdf(
        path,
        [
            [header, ("Chapter One", 22), *_body()],
            [header, *_body()],
            [header, ("Chapter Two", 22), *_body()],
            [header, *_body()],
            [header, *_body()],
        ],
    )

    assert [e.title for e in extract_outline(str(path), [])] == ["Chapter One", "Chapter Two"]


def test_a_single_heading_is_not_an_outline(tmp_path):
    path = tmp_path / "book.pdf"
    _make_pdf(path, [[("Only Heading", 22), *_body()], _body()])

    assert extract_outline(str(path), []) == []


def test_a_pdf_without_any_structure_has_no_outline(tmp_path):
    path = tmp_path / "flat.pdf"
    _make_pdf(path, [_body(), _body()])

    assert extract_outline(str(path), []) == []


def test_docx_headings_become_the_outline_with_unit_positions(tmp_path):
    doc = DocxDocument()
    doc.add_heading("Chapter One", level=1)
    doc.add_paragraph("text a")
    doc.add_heading("Section One", level=2)
    doc.add_paragraph("text b")
    doc.add_heading("Chapter Two", level=1)
    doc.add_paragraph("text c")
    path = tmp_path / "book.docx"
    doc.save(str(path))

    outline = extract_outline(str(path), read_docx(str(path)))

    assert outline == [
        OutlineEntry(1, "Chapter One", 0),
        OutlineEntry(2, "Section One", 1),
        OutlineEntry(1, "Chapter Two", 2),
    ]


def test_html_headings_become_the_outline_with_unit_positions(tmp_path):
    html = (
        "<html><body>"
        "<h1>Chapter One</h1><p>a</p>"
        "<h2>Section One</h2><p>b</p>"
        "<h1>Chapter Two</h1><p>c</p>"
        "</body></html>"
    )
    path = tmp_path / "book.html"
    path.write_text(html, encoding="utf-8")

    outline = extract_outline(str(path), read_html(str(path)))

    assert [(e.level, e.title, e.anchor) for e in outline] == [
        (1, "Chapter One", 0),
        (2, "Section One", 1),
        (1, "Chapter Two", 2),
    ]


def test_formats_without_a_notion_of_chapters_have_no_outline():
    assert extract_outline("slides.pptx", []) == []
    assert extract_outline("notes.txt", []) == []


def test_an_unreadable_pdf_yields_no_outline_instead_of_raising(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"this is not a pdf")

    assert extract_outline(str(path), []) == []


def test_clean_outline_drops_empty_deep_and_duplicate_entries():
    raw = [
        OutlineEntry(1, "  ", 1),
        OutlineEntry(1, "Chapter  One", 1),
        OutlineEntry(1, "Chapter One", 1),
        OutlineEntry(4, "Too deep", 2),
        OutlineEntry(2, "Section", 2),
    ]

    assert clean_outline(raw) == [OutlineEntry(1, "Chapter One", 1), OutlineEntry(2, "Section", 2)]


def test_clean_outline_strips_nul_bytes_which_postgres_refuses_to_store():
    raw = [OutlineEntry(1, "Chap\x00ter One", 1), OutlineEntry(1, "\x00", 2), OutlineEntry(1, "Two", 3)]

    assert clean_outline(raw) == [OutlineEntry(1, "Chapter One", 1), OutlineEntry(1, "Two", 3)]


def test_clean_outline_needs_at_least_two_sections():
    assert clean_outline([OutlineEntry(1, "Only", 1)]) == []


def test_clean_outline_orders_entries_by_position():
    raw = [OutlineEntry(1, "Second", 5), OutlineEntry(1, "First", 2)]

    assert [e.title for e in clean_outline(raw)] == ["First", "Second"]
