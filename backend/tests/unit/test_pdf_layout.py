"""Measured slide repairs: adjacent runs, indices, fractions and font decoding."""

import re
import uuid

import pymupdf
import pytest

from app.ingestion.document_readers import read_document
from app.ingestion.pdf_layout import map_font_text, page_lines


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


@pytest.fixture
def page():
    doc = pymupdf.open()
    yield doc.new_page()
    doc.close()


def test_a_formula_written_font_by_font_reads_back_as_one_line_in_reading_order(page):
    page.insert_text((50, 100), "x", fontname="tiit", fontsize=20)
    page.insert_text((110, 100), "1", fontname="tiro", fontsize=20)
    page.insert_text((80, 100), "=", fontname="symb", fontsize=20)

    assert [_compact(line) for line in page_lines(page)] == ["x=1"]


def test_a_smaller_raised_glyph_is_a_superscript(page):
    page.insert_text((50, 100), "x", fontname="tiit", fontsize=20)
    page.insert_text((62, 91), "2", fontname="tiro", fontsize=11)
    page.insert_text((80, 100), "=", fontname="symb", fontsize=20)
    page.insert_text((100, 100), "4", fontname="tiro", fontsize=20)

    assert [_compact(line) for line in page_lines(page)] == ["x^2=4"]


def test_a_superscript_of_several_glyphs_is_grouped_so_it_is_not_read_as_a_different_sum(page):
    page.insert_text((50, 100), "e", fontname="tiit", fontsize=20)
    page.insert_text((60, 91), "n", fontname="tiit", fontsize=11)
    page.insert_text((66, 91), "+", fontname="symb", fontsize=11)
    page.insert_text((73, 91), "1", fontname="tiro", fontsize=11)
    page.insert_text((90, 100), "=", fontname="symb", fontsize=20)
    page.insert_text((110, 100), "y", fontname="tiit", fontsize=20)

    assert [_compact(line) for line in page_lines(page)] == ["e^(n+1)=y"]


def test_glyphs_above_and_below_a_drawn_bar_are_a_fraction(page):
    page.insert_text((60, 90), "a", fontname="tiit", fontsize=14)
    page.insert_text((80, 90), "1", fontname="tiro", fontsize=14)
    page.insert_text((70, 90), "+", fontname="symb", fontsize=14)
    page.draw_line((55, 95), (95, 95), width=1)
    page.insert_text((72, 110), "b", fontname="tiit", fontsize=14)
    page.insert_text((30, 99), "y", fontname="tiit", fontsize=14)
    page.insert_text((42, 99), "=", fontname="symb", fontsize=14)

    assert [_compact(line) for line in page_lines(page)] == ["y=(a+1)/b"]


def test_a_lower_index_of_the_line_above_is_not_attached_to_the_text_below_it(page):
    page.insert_text((50, 100), "x", fontname="tiit", fontsize=16)
    page.insert_text((60, 104), "n", fontname="tiit", fontsize=9)
    page.insert_text((75, 100), "=", fontname="symb", fontsize=16)
    page.insert_text((90, 100), "0", fontname="tiro", fontsize=16)
    # Phông base-14 không mã hóa được chữ tiếng Việt, nên câu ở đây viết không dấu.
    page.insert_text((50, 120), "Then the sequence converges", fontname="helv", fontsize=16)

    lines = page_lines(page)

    assert [_compact(line) for line in lines] == ["x_n=0", "Thenthesequenceconverges"]


def test_plain_text_lines_keep_the_order_and_text_of_the_ordinary_reading(page):
    page.insert_text((50, 80), "Operating systems manage hardware.", fontname="helv", fontsize=12)
    page.insert_text((50, 100), "A process is a program in execution.", fontname="helv", fontsize=12)
    page.insert_text((50, 120), "Threads share the address space.", fontname="helv", fontsize=12)

    expected = [line.strip() for line in page.get_text().splitlines() if line.strip()]

    assert page_lines(page) == expected


def test_overlapping_callouts_on_code_without_math_are_left_as_they_were(page):
    """Hộp chú thích đè lên mã nguồn cùng một hàng, kèm nét chỉ ngang: không phải công thức,
    và trộn chúng lại thì chữ hai bên đan vào nhau, còn nét chỉ thành gạch phân số."""
    page.insert_text((50, 100), "<title>Page Title</title>", fontname="cour", fontsize=14)
    page.insert_text((150, 104), "meta information about the page", fontname="helv", fontsize=12)
    page.insert_text((50, 130), "<body>", fontname="cour", fontsize=14)
    page.draw_line((60, 106), (320, 106), width=1)

    lines = page_lines(page)

    assert "<title>Page Title</title>" in lines
    assert "meta information about the page" in lines
    assert all("/" not in line.replace("</", "") for line in lines)


def test_text_drawn_twice_on_top_of_itself_is_read_once(page):
    for _ in range(2):
        page.insert_text((50, 80), "Indefinite integrals", fontname="helv", fontsize=20)

    assert page_lines(page) == ["Indefinite integrals"]


def test_a_page_with_no_text_has_no_lines(page):
    page.draw_rect(pymupdf.Rect(10, 10, 100, 100))

    assert page_lines(page) == []


@pytest.mark.parametrize(
    "encoded, font, expected",
    [
        ("", "Symbol", "∫=+−"),
        ("", "Symbol", "αβπ"),
        ("", "SymbolMT", "≠≤≥⇒"),
        ("abp", "Symbol", "αβπ"),
    ],
)
def test_symbol_font_codes_become_the_symbols_they_draw(encoded, font, expected):
    assert map_font_text(encoded, font) == expected


def test_ordinary_fonts_are_not_remapped():
    assert map_font_text("abc ", "Times New Roman") == "abc "


def test_dingbat_bullets_become_a_bullet():
    assert map_font_text("", "Wingdings") == "•"


def test_vni_fonts_with_valid_unicode_are_left_unchanged():
    assert map_font_text("Chương trình", "VNI-Times") == "Chương trình"


def test_read_document_uses_the_rebuilt_lines_for_pdf_pages(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 60), "Bang tich phan co ban", fontname="helv", fontsize=16)
    page.insert_text((50, 100), "x", fontname="tiit", fontsize=20)
    page.insert_text((110, 100), "1", fontname="tiro", fontsize=20)
    page.insert_text((80, 100), "=", fontname="symb", fontsize=20)
    path = tmp_path / "cong_thuc.pdf"
    doc.save(path)
    doc.close()

    units = read_document(str(path), course_id=str(uuid.uuid4()), source_id=None)

    assert "x = 1" in "\n".join(u.text for u in units)


# --- tiêu đề trang -------------------------------------------------------------


def _deck(tmp_path, pages):
    """pages: danh sách các trang, mỗi trang là danh sách (y, chữ, cỡ chữ) theo đúng thứ tự ghi."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        for y, text, size in lines:
            page.insert_text((50, y), text, fontname="helv", fontsize=size)
    path = tmp_path / "deck.pdf"
    doc.save(path)
    doc.close()
    return [u.locator["heading"] for u in read_document(str(path), course_id=str(uuid.uuid4()), source_id=None)]


def test_the_page_heading_is_the_largest_line_even_when_a_footer_is_written_first(tmp_path):
    headings = _deck(tmp_path, [[
        (780, "Silberschatz, Galvin and Gagne 2018", 9),
        (80, "Process Scheduling", 28),
        (130, "The scheduler selects a process to run next.", 14),
        (160, "Queues hold processes waiting for the CPU.", 14),
    ]])

    assert headings == ["Process Scheduling"]


def test_a_title_repeated_on_every_slide_gives_way_to_the_slide_own_title(tmp_path):
    pages = [
        [(60, "Indefinite integrals", 30), (110, f"Substitution rule {i}", 22), (160, "Body text of the slide", 14),
         (190, "More body text here", 14)]
        for i in range(1, 6)
    ]

    assert _deck(tmp_path, pages) == [f"Substitution rule {i}" for i in range(1, 6)]


def test_a_page_whose_lines_are_all_the_same_size_has_no_heading_rather_than_a_wrong_one(tmp_path):
    headings = _deck(tmp_path, [[
        (80, "- first bullet of the slide", 14),
        (110, "- second bullet of the slide", 14),
    ]])

    assert headings == [None]
