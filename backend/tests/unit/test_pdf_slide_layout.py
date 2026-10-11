"""Slide layout must keep code, columns and table rows in their source order."""

import pymupdf
import pytest

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
