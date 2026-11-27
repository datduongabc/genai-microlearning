"""Hợp đồng của tầng dựng dòng PDF: công thức bị tách theo phông phải đọc lại được thành một
dòng, ký hiệu của phông cũ phải ra Unicode, và văn bản thường không được bị động tới.

Các PDF ở đây được tạo bằng PyMuPDF và ghi chữ theo đúng cách MathType ghi một công thức: mọi
chữ của một phông trước, rồi mới tới phông khác, lệch khỏi thứ tự đọc. Chính cách ghi đó làm
cách đọc theo khối trả mỗi ký hiệu một dòng.
"""

import re
import uuid

import pymupdf
import pytest

from app.ingestion.document_readers import read_document
from app.ingestion.pdf_layout import (
    _line_text,
    map_font_text,
    page_lines,
    suspect_formula_lines,
    vni_to_unicode,
)


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


def test_a_bulleted_fraction_over_a_drawn_bar_is_still_a_fraction(page):
    page.insert_text((15, 99), "\xb7", fontname="symb", fontsize=14)
    page.insert_text((60, 90), "a", fontname="tiit", fontsize=14)
    page.insert_text((80, 90), "1", fontname="tiro", fontsize=14)
    page.insert_text((70, 90), "+", fontname="symb", fontsize=14)
    page.draw_line((55, 95), (95, 95), width=1)
    page.insert_text((72, 110), "b", fontname="tiit", fontsize=14)
    page.insert_text((30, 99), "y", fontname="tiit", fontsize=14)
    page.insert_text((42, 99), "=", fontname="symb", fontsize=14)

    assert page_lines(page) == ["• y = (a + 1)/b"]


def test_a_named_function_over_a_drawn_bar_is_still_a_fraction(page):
    page.insert_text((20, 99), "lim", fontname="tiro", fontsize=14)
    page.insert_text((60, 90), "arctan", fontname="tiro", fontsize=14)
    page.insert_text((102, 90), "x", fontname="tiit", fontsize=14)
    page.draw_line((55, 95), (112, 95), width=1)
    page.insert_text((80, 110), "x", fontname="tiit", fontsize=14)
    page.insert_text((120, 99), "=", fontname="symb", fontsize=14)
    page.insert_text((135, 99), "1", fontname="tiro", fontsize=14)

    assert [_compact(line) for line in page_lines(page)] == ["lim(arctanx)/x=1"]


def test_a_drawn_radical_sign_wraps_what_is_under_its_bar(page):
    # Dấu căn của MathType: nét chéo đi xuống rồi đi lên chạm đầu trái của vạch trên.
    page.draw_line((40, 95), (43, 93), width=0.5)
    page.draw_line((43, 93), (47, 102), width=0.5)
    page.draw_line((47, 102), (52, 82), width=0.5)
    page.draw_line((52, 82), (70, 82), width=0.5)
    page.insert_text((55, 98), "x", fontname="tiit", fontsize=16)
    page.insert_text((80, 98), "=", fontname="symb", fontsize=16)
    page.insert_text((96, 98), "3", fontname="tiro", fontsize=16)

    assert [_compact(line) for line in page_lines(page)] == ["√x=3"]


def test_a_rule_under_a_radical_is_not_mistaken_for_a_fraction_bar(page):
    page.insert_text((20, 70), "u", fontname="tiit", fontsize=16)
    page.insert_text((34, 70), "=", fontname="symb", fontsize=16)
    page.draw_line((40, 95), (43, 93), width=0.5)
    page.draw_line((43, 93), (47, 102), width=0.5)
    page.draw_line((47, 102), (52, 82), width=0.5)
    page.draw_line((52, 82), (70, 82), width=0.5)
    page.insert_text((55, 98), "x", fontname="tiit", fontsize=16)

    assert all("/" not in line for line in page_lines(page))


def _char_box(page, char):
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for c in span["chars"]:
                    if c["c"] == char:
                        return pymupdf.Rect(c["bbox"])
    raise AssertionError(char)


def test_a_tex_radical_glyph_with_a_bar_from_its_edge_wraps_what_is_under_the_bar(page):
    # TeX: dấu căn là một glyph của phông, vạch trên là một nét ngang bắt đầu ngay mép phải glyph.
    page.insert_text((40, 100), "\xd6", fontname="symb", fontsize=16)
    sign = _char_box(page, "\xd6")
    page.insert_text((sign.x1 + 1, 100), "x", fontname="tiit", fontsize=16)
    page.insert_text((sign.x1 + 20, 100), "=", fontname="symb", fontsize=16)
    page.insert_text((sign.x1 + 36, 100), "3", fontname="tiro", fontsize=16)
    top = _char_box(page, "x").y0 + 1
    page.draw_line((sign.x1, top), (sign.x1 + 12, top), width=0.5)

    assert [_compact(line) for line in page_lines(page)] == ["√x=3"]


def test_a_fraction_bar_touching_a_big_radical_glyph_stays_inside_the_radical(page):
    # Dấu căn lớn bao một phân số: gạch phân số cũng bắt đầu ở mép glyph nhưng thấp hơn vạch trên.
    page.insert_text((40, 100), "\xd6", fontname="symb", fontsize=30)
    sign = _char_box(page, "\xd6")
    page.insert_text((sign.x1 + 2, 92), "1", fontname="tiro", fontsize=12)
    page.insert_text((sign.x1 + 2, 108), "n", fontname="tiit", fontsize=12)
    page.draw_line((sign.x1 + 0.5, 96), (sign.x1 + 9, 96), width=0.5)
    top = _char_box(page, "1").y0 - 1
    page.draw_line((sign.x1, top), (sign.x1 + 12, top), width=0.5)
    page.insert_text((sign.x1 + 20, 100), "=", fontname="symb", fontsize=16)
    page.insert_text((sign.x1 + 36, 100), "y", fontname="tiit", fontsize=16)

    assert [_compact(line) for line in page_lines(page)] == ["√(1/n)=y"]


def test_a_radical_bar_just_under_a_longer_fraction_bar_keeps_its_radical(page):
    # pdflatex \frac{1}{\sqrt{n}}: glyph căn của TeX nhô lên quá gạch phân số, còn vạch trên của
    # căn ở mẫu số nằm sát dưới gạch phân số dài hơn nó.
    page.insert_text((62, 90), "1", fontname="tiro", fontsize=14)
    page.insert_text((60, 100), "\xd6", fontname="symb", fontsize=14)
    sign = _char_box(page, "\xd6")
    page.insert_text((sign.x1 + 1, 112), "n", fontname="tiit", fontsize=14)
    page.draw_line((sign.x0, 96.3), (sign.x1 + 9, 96.3), width=0.5)
    page.draw_line((sign.x1, 98.3), (sign.x1 + 9, 98.3), width=0.5)
    page.insert_text((30, 101), "y", fontname="tiit", fontsize=14)
    page.insert_text((42, 101), "=", fontname="symb", fontsize=14)

    assert [_compact(line) for line in page_lines(page)] == ["y=1/(√n)"]


def test_limits_under_lim_follow_it_even_when_they_start_further_left(page):
    page.insert_text((50, 100), "lim", fontname="tiro", fontsize=16)
    page.insert_text((46, 112), "n", fontname="tiit", fontsize=9)
    page.insert_text((52, 112), "=", fontname="symb", fontsize=9)
    page.insert_text((60, 112), "1", fontname="tiro", fontsize=9)
    page.insert_text((80, 100), "u", fontname="tiit", fontsize=16)
    page.insert_text((100, 100), "=", fontname="symb", fontsize=16)
    page.insert_text((116, 100), "a", fontname="tiit", fontsize=16)

    lines = [_compact(line) for line in page_lines(page)]

    assert lines == ["lim_(n=1)u=a"]


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
    assert map_font_text(encoded, font) == (expected, False)


@pytest.mark.parametrize(
    "encoded, font, expected",
    [
        ("R", "CMEX10", ("∫", False)),
        ("Z", "CMEX10", ("∫", False)),
        ("P", "CMEX10", ("∑", False)),
        ("q", "CMEX10", ("√", False)),
        ("\x12", "CMEX10", ("(", True)),
        ("\x13", "CMEX10", (")", True)),
        ("RNZ", "MSBM10", ("ℝℕℤ", False)),
    ],
)
def test_tex_extension_and_blackboard_codes_become_the_symbols_they_draw(encoded, font, expected):
    # CMEX và msbm của TeX không kèm bảng chuyển mã: PyMuPDF trả nguyên mã glyph.
    assert map_font_text(encoded, font) == expected


def test_cmex_delimiters_that_a_to_unicode_map_already_decoded_are_kept():
    assert map_font_text("(", "CMEX10") == ("(", False)


def test_ordinary_fonts_are_not_remapped():
    assert map_font_text("abc ", "Times New Roman") == ("abc ", False)


@pytest.mark.parametrize("piece, bracket", [("", "("), ("", ")"), ("", "("), ("", ")")])
def test_pieces_of_a_tall_bracket_are_flagged_as_brackets(piece, bracket):
    assert map_font_text(piece, "Symbol") == (bracket, True)


def test_dingbat_bullets_become_a_bullet():
    assert map_font_text("", "Wingdings") == ("•", False)


@pytest.mark.parametrize(
    "encoded, expected",
    [
        ("Chöông", "Chương"),
        ("Ñaïi hoïc", "Đại học"),
        ("thöôøng xuyeân", "thường xuyên"),
        ("Kyø voïng cuûa bieán ngaãu nhieân", "Kỳ vọng của biến ngẫu nhiên"),
        ("LÔØI GIAÛI", "LỜI GIẢI"),
        ("Ñònh nghóa", "Định nghĩa"),
        ("aên nhaèm", "ăn nhằm"),
    ],
)
def test_vni_encoded_vietnamese_becomes_unicode(encoded, expected):
    assert vni_to_unicode(encoded) == expected


def test_text_without_vni_codes_is_unchanged_by_the_vni_decoder():
    text = "Operating systems and processes"

    assert vni_to_unicode(text) == text


def test_symbol_font_bullets_keep_the_ordinary_line_reading(page):
    # The Symbol font makes the region math-like; its bullets must not send it to formula rebuilding.
    for y, text in [(100, "Each process has a state"), (120, "The scheduler picks one")]:
        page.insert_text((50, y - 3), "\xb7", fontname="symb", fontsize=10)
        page.insert_text((70, y), text, fontname="helv", fontsize=14)

    assert page_lines(page) == ["•", "Each process has a state", "•", "The scheduler picks one"]


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


def test_a_deck_title_drawn_with_a_leading_space_on_some_slides_is_still_the_deck_title(tmp_path):
    pages = [
        [(60, (" " if i % 2 else "") + "Linked lists", 30), (110, f"Removal {i}", 22),
         (160, "Body text of the slide", 14), (190, "More body text here", 14)]
        for i in range(1, 6)
    ]

    assert _deck(tmp_path, pages) == [f"Removal {i}" for i in range(1, 6)]


def test_a_large_bullet_drawn_after_a_space_is_not_a_heading(tmp_path):
    headings = _deck(tmp_path, [[
        (80, " - Stack frames", 28),
        (130, "Each call pushes a frame on the stack.", 14),
        (160, "Returning pops the frame again.", 14),
    ]])

    assert headings == [None]


def test_a_page_whose_lines_are_all_the_same_size_has_no_heading_rather_than_a_wrong_one(tmp_path):
    headings = _deck(tmp_path, [[
        (80, "- first bullet of the slide", 14),
        (110, "- second bullet of the slide", 14),
    ]])

    assert headings == [None]


def test_vni_decoder_requires_evidence_of_legacy_encoding(monkeypatch, page):
    from app.ingestion import pdf_layout

    page.insert_text((50, 80), "Vieät Nam", fontname="helv")
    original = pymupdf.Page.get_text

    def legacy_text(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if args and args[0] == "rawdict":
            for block in result["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        span["font"] = "VNI-Times"
        return result

    monkeypatch.setattr(pymupdf.Page, "get_text", legacy_text)
    assert page_lines(page) == ["Việt Nam"]
    assert pdf_layout.map_font_text("Hòa bình", "VNI-Times") == ("Hòa bình", False)
    assert pdf_layout.vni_to_unicode("TRÖÔØNG ÑAÏI HOÏC") == "TRƯỜNG ĐẠI HỌC"


# --- công thức còn vỡ ----------------------------------------------------------


@pytest.mark.parametrize(
    "lines",
    [
        ["(x_(n+1))/x_n = (( )^(n+1))/(( )^n)"],
        ["I = lim ((n^2 + 1)( + n√n))/(n + 1)"],
        ["1 + 1/n) .n/(n+1) = 1."],
        ["lim_(x→0) f(x) = A", "x→0", "x→0"],
    ],
)
def test_a_formula_with_an_empty_group_a_dangling_operator_or_stray_limits_is_suspect(lines):
    assert suspect_formula_lines(lines)


@pytest.mark.parametrize(
    "lines",
    [
        ["lim_(n→∞) (√(n^2 + 1) − n)/(√(n + 1) − √n)."],
        ["f (x) trên [a, +∞) và được ký hiệu là ∫_a^(+∞)f (x)dx."],
        ["1)A/(x − a); 2)A/((x − a)^k) (k = 2, 3, . . .);"],
        ["thì luôn có bất đẳng thức (1 + h)^n > 1 + nh với"],
        ["Ví dụ", "= 0."],
        ["x→0"],
    ],
)
def test_a_rebuilt_formula_an_interval_a_numbered_list_or_one_short_line_is_not_suspect(lines):
    assert suspect_formula_lines(lines) == []


def test_the_edge_of_a_rounded_box_is_not_a_fraction_bar_between_the_text_in_it_and_the_footer(page):
    # A Beamer block (Calculus 1, chapter 2, page 5): the box's lower edge runs as wide as the last line in it and
    # the running footer just under it, which were read as one fraction "(line)/(footer)".
    page.draw_rect(pymupdf.Rect(20, 200, 250, 263), radius=0.05, width=0.5)
    x = 25
    for text, font in (("The set (a - ", "tiro"), ("e", "symb"), (", a + ", "tiro"), ("e", "symb"),
                       (") is a neighbourhood of a, written O(a, ", "tiro"), ("e", "symb"), (").", "tiro")):
        page.insert_text((x, 260), text, fontname=font, fontsize=9)
        x += pymupdf.get_text_length(text, fontname=font, fontsize=9)
    page.insert_text((25, 270), "Dr. Le Xuan Dai (HCMUT)   LIMITS AND CONTINUITY OF A FUNCTION   5 / 53",
                     fontname="tiro", fontsize=6)

    lines = page_lines(page)

    assert not any("/(Dr." in line for line in lines), lines
    assert any(line.startswith("Dr. Le Xuan Dai") for line in lines), lines


def _span(font, text, x0, x1, size=17.2, y=100.0):
    """A span as PyMuPDF's rawdict gives it, its characters spread evenly from x0 to x1."""
    step = (x1 - x0) / max(len(text), 1)
    chars = [{"c": c, "bbox": (x0 + i * step, y - size, x0 + (i + 1) * step, y), "origin": (x0 + i * step, y)}
             for i, c in enumerate(text)]
    return {"font": font, "size": size, "flags": 0, "chars": chars, "origin": (x0, y)}


def test_a_gap_between_a_word_and_a_formula_in_another_font_keeps_its_space():
    # The spans of Calculus 1, chapter 1, page 8: TeX spaces "số" from the formula after it by position, with no
    # space character, and joined as written the line read "Nếu sốh > −1".
    spans = [_span("vnss17", "Nếu", 182.8, 209.7), _span("vnss17", " ", 209.7, 215.0),
             _span("vnss17", "số", 215.0, 229.1), _span("CMSSI17", "h", 234.3, 242.7),
             _span("CMMI12", " >", 242.7, 260.9), _span("CMSY10", " −", 260.9, 279.1), _span("CMSS17", "1", 279.1, 287.2)]

    assert _line_text(spans, frozenset()) == "Nếu số h > −1"


def test_a_gap_inside_one_span_is_a_space_too():
    # "hội tụ đến" on Calculus 1, chapter 1, page 29: one span "tụđến", with the gap of a space between ụ and đ.
    chars = [("t", 159.4, 165.1), ("ụ", 165.1, 173.5), ("đ", 178.8, 187.1), ("ế", 187.1, 194.2), ("n", 194.2, 202.6)]
    span = {"font": "vnss17", "size": 17.2, "flags": 0, "origin": (159.4, 100.0),
            "chars": [{"c": c, "bbox": (x0, 83, x1, 100), "origin": (x0, 100)} for c, x0, x1 in chars]}

    assert _line_text([span], frozenset()) == "tụ đến"


def test_letters_of_one_word_in_two_fonts_are_not_split():
    spans = [_span("vnss17", "đẳ", 46.6, 62.0), _span("vnssb17", "ng", 62.1, 78.9)]

    assert _line_text(spans, frozenset()) == "đẳng"


@pytest.mark.parametrize("code, arrow", [("", "→"), ("", "→"), ("", "←"), ("", "→"), ("", "↑")])
def test_a_wingdings_arrow_stays_an_arrow(code, arrow):
    # "A ⇒ B" read as "A • B" loses what leads to what.
    assert map_font_text(code, "Wingdings") == (arrow, False)


def test_a_wingdings_arrowhead_used_as_a_bullet_stays_a_bullet():
    assert map_font_text("", "Wingdings") == ("•", False)
