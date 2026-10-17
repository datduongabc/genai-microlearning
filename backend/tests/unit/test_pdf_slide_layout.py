"""Slide layout must keep code, columns and table rows in their source order."""

import pymupdf
import pytest

from app.ingestion.chunker import chunk_units
from app.ingestion.document_readers import read_pdf
from app.ingestion.pdf_layout import page_lines


@pytest.fixture
def page():
    with pymupdf.open() as doc:
        yield doc.new_page()


def test_code_written_in_separate_adjacent_runs_is_joined(page):
    page.insert_text((50, 100), "end", fontsize=12)
    page.insert_text((81, 100), "list", fontsize=12)
    assert page_lines(page) == ["end list"]


def test_table_rules_are_not_rebuilt_as_fractions(page):
    page.insert_text((50, 90), "Efficiency", fontsize=14)
    page.insert_text((50, 110), "logarithmic", fontsize=14)
    page.insert_text((113, 90), "a", fontname="symb", fontsize=14)
    page.draw_line((45, 95), (120, 95))
    assert page_lines(page) == ["Efficiency α", "logarithmic"]


def test_same_letter_in_distinct_table_cells_is_kept(page):
    page.insert_text((50, 100), "F")
    page.insert_text((250, 100), "F")
    assert page_lines(page) == ["F", "F"]


def test_two_columns_keep_the_authored_order(page):
    for xy, text in [((50, 80), "Left column one"), ((50, 110), "Left column two"),
                     ((300, 80), "Right column one"), ((300, 110), "Right column two")]:
        page.insert_text(xy, text)
    assert page_lines(page) == ["Left column one", "Left column two", "Right column one", "Right column two"]


def test_pdf_reader_keeps_explicit_code_indentation(tmp_path):
    path = tmp_path / "code.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), "if (ready) {\n    run();\n}", fontname="cour", fontsize=12)
        doc.save(path)
    assert "\n    run();\n" in read_pdf(str(path))[0].text


def test_footer_touching_the_last_code_line_does_not_reorder_the_code(page):
    # A footer is emitted before the body by Office PDFs. Its bounding box can
    # touch the closing brace; grouping ordinary overlaps moved that brace first.
    page.insert_text((50, 130), "Slide footer with long text", fontsize=10)
    page.insert_text((150, 80), "if (ready) {", fontname="cour", fontsize=12)
    page.insert_text((150, 100), "    run();", fontname="cour", fontsize=12)
    page.insert_text((150, 130), "}", fontname="cour", fontsize=12)
    assert page_lines(page) == ["Slide footer with long text", "if (ready) {", "    run();", "}"]


def _words(page, x, y, text, fontname, fontsize=8):
    # Beamer listings draw every word as its own run.
    for word in text.split():
        page.insert_text((x, y), word, fontname=fontname, fontsize=fontsize)
        x += pymupdf.get_text_length(word + " ", fontname=fontname, fontsize=fontsize)


def test_a_numbered_listing_keeps_each_line_number_with_its_code(page, tmp_path):
    for y, number, code in [(100, "5", "public:"), (110, "6", "virtual int get(int index);")]:
        page.insert_text((21, y), number, fontsize=6)
        _words(page, 50, y, code, "cour")
    assert page_lines(page) == ["5 public:", "6 virtual int get(int index);"]
    page.insert_text((21, 50), "Array List implementation", fontsize=22)
    path = tmp_path / "listing.pdf"
    page.parent.save(path)
    units = read_pdf(str(path))
    assert len(units) == 1
    assert units[0].locator["heading"] == "Array List implementation"
    assert "5 public:\n6 virtual int get(int index);" in units[0].text
    chunks = chunk_units(units, title="Lists")
    assert chunks
    for chunk in chunks:
        assert all(span["heading"] == "Array List implementation"
                   for span in chunk.locator.get("spans", [chunk.locator]))
        assert chunk.content.startswith("Lists › Array List implementation")


def test_a_cost_comment_in_its_own_column_stays_on_its_code_line(page):
    for y, code, cost in [(100, "currentMax = a[0]", "//2"), (111, "i = 1", "//2")]:
        page.insert_text((60, y), code, fontname="cour", fontsize=9)
        page.insert_text((218, y), cost, fontname="coit", fontsize=9)
    assert page_lines(page) == ["currentMax = a[0] //2", "i = 1 //2"]


def test_a_number_in_a_separate_table_column_is_not_joined_to_the_next_cell(page):
    page.insert_text((50, 100), "1", fontsize=10)
    page.insert_text((250, 100), "FCFS", fontsize=10)
    assert page_lines(page) == ["1", "FCFS"]


def test_an_italic_code_comment_reaching_the_slide_navigation_keeps_the_code_together(page):
    for y, text in [(108, "Array implementation"), (116, "How to store?"), (150, "Operations")]:
        page.insert_text((300, y), text, fontsize=6)
    page.insert_text((10, 18), "Removal: Implementation in C++", fontsize=11)
    _words(page, 21, 102, "1 class List {", "cour")
    _words(page, 21, 112, "2 // Declaration of attributes, constructor and destructor.", "coit")
    _words(page, 21, 122, "3 public:", "cour")
    assert page_lines(page) == [
        "Array implementation", "How to store?", "Operations", "Removal: Implementation in C++",
        "1 class List {", "2 // Declaration of attributes, constructor and destructor.", "3 public:",
    ]


@pytest.mark.parametrize("fontname", ["helv", "cour"])
def test_numbered_prose_heading_on_a_listing_page_is_preserved(page, tmp_path, fontname):
    page.insert_text((21, 50), "List operations", fontsize=22)
    page.insert_text((21, 80), "3. Removal", fontsize=14)
    page.insert_text((21, 110), "1", fontsize=6)
    _words(page, 50, 110, "public:", "cour")
    page.insert_text((21, 140), "4. Summary", fontsize=14)
    page.insert_text((21, 170), "The list retains its remaining elements.", fontname=fontname, fontsize=10)
    path = tmp_path / "sections.pdf"
    page.parent.save(path)
    units = read_pdf(str(path))
    assert [unit.locator["heading"] for unit in units] == [
        "List operations", "3. Removal", "4. Summary",
    ]
    assert "1 public:" in units[1].text
    assert [span["heading"] for chunk in chunk_units(units)
            for span in chunk.locator.get("spans", [chunk.locator])] == [
        "List operations", "3. Removal", "4. Summary",
    ]
