"""What a slide deck gives the pipeline: the text of every slide, whatever holds it (a text box, a
table, a group of shapes), in the order it is read, with the speaker notes kept apart, and without
labels that are not part of the deck. Decks are built with python-pptx, the same library the reader
uses, so a test never depends on a file that is not in the repository."""

from pptx import Presentation
from pptx.util import Inches

from app.ingestion.document_readers import read_document, read_pptx

TITLE_ONLY = 5
BLANK = 6


def _deck(tmp_path, build):
    prs = Presentation()
    build(prs)
    path = tmp_path / "deck.pptx"
    prs.save(str(path))
    return str(path)


def _text_box(shapes, text, top=2):
    box = shapes.add_textbox(Inches(1), Inches(top), Inches(6), Inches(1))
    box.text_frame.text = text
    return box


def _table(shapes, rows, top=3):
    table = shapes.add_table(len(rows), len(rows[0]), Inches(1), Inches(top), Inches(6), Inches(2)).table
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    return table


def _lines(unit):
    return [line for line in unit.text.split("\n") if line.strip()]


def test_the_title_and_the_body_of_a_slide_are_its_text_in_reading_order(tmp_path):
    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[TITLE_ONLY])
        slide.shapes.title.text = "Lập lịch CPU"
        _text_box(slide.shapes, "First-Come, First-Served là chính sách đơn giản")

    units = read_pptx(_deck(tmp_path, build))

    assert len(units) == 1
    assert _lines(units[0])[0] == "Lập lịch CPU"
    assert "First-Come, First-Served" in units[0].text
    assert units[0].text.index("Lập lịch CPU") < units[0].text.index("First-Come")
    assert units[0].locator == {"slide_index": 1, "slide_title": "Lập lịch CPU"}


def test_no_label_that_is_not_in_the_deck_is_added_to_the_text(tmp_path):
    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[TITLE_ONLY])
        slide.shapes.title.text = "Tiêu đề"
        _text_box(slide.shapes, "Nội dung")
        slide.notes_slide.notes_text_frame.text = "Ghi chú của giảng viên"

    text = read_pptx(_deck(tmp_path, build))[0].text

    for label in ("[Tieu de]", "[Noi dung]", "[Ghi chu]"):
        assert label not in text


def test_a_table_is_read_row_by_row_with_the_heading_of_each_column(tmp_path):
    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[TITLE_ONLY])
        slide.shapes.title.text = "Ví dụ FCFS"
        _table(slide.shapes, [["Process", "Burst Time"], ["P1", "24"], ["P2", "3"], ["P3", "3"]])

    lines = _lines(read_pptx(_deck(tmp_path, build))[0])

    assert any("Process: P1" in line and "Burst Time: 24" in line for line in lines)
    assert any("Process: P2" in line and "Burst Time: 3" in line for line in lines)
    assert any("Process: P3" in line and "Burst Time: 3" in line for line in lines)


def test_a_slide_with_nothing_but_a_table_is_not_lost(tmp_path):
    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[BLANK])
        _table(slide.shapes, [["Khái niệm", "Định nghĩa"], ["Convoy effect", "tiến trình ngắn chờ sau tiến trình dài"]])

    units = read_pptx(_deck(tmp_path, build))

    assert len(units) == 1
    assert "Convoy effect" in units[0].text
    assert "tiến trình ngắn chờ sau tiến trình dài" in units[0].text


def test_empty_cells_are_left_out_and_never_break_the_row(tmp_path):
    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[BLANK])
        _table(slide.shapes, [["A", "B", "C"], ["1", "", "3"], ["", "", ""]])

    lines = _lines(read_pptx(_deck(tmp_path, build))[0])

    assert any("A: 1" in line and "C: 3" in line and "B:" not in line for line in lines)
    assert len(lines) == 1


def test_text_inside_a_group_of_shapes_is_read(tmp_path):
    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[BLANK])
        group = slide.shapes.add_group_shape()
        _text_box(group.shapes, "Bên trong nhóm shape", top=1)
        _text_box(group.shapes, "Cũng trong nhóm", top=2)
        _text_box(slide.shapes, "Ngoài nhóm", top=4)

    text = read_pptx(_deck(tmp_path, build))[0].text

    assert "Bên trong nhóm shape" in text
    assert "Cũng trong nhóm" in text
    assert "Ngoài nhóm" in text


def test_a_group_inside_a_group_is_read_too(tmp_path):
    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[BLANK])
        outer = slide.shapes.add_group_shape()
        inner = outer.shapes.add_group_shape()
        _text_box(inner.shapes, "Lồng hai lớp")

    assert "Lồng hai lớp" in read_pptx(_deck(tmp_path, build))[0].text


def test_speaker_notes_are_kept_and_kept_apart_from_the_body(tmp_path):
    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[TITLE_ONLY])
        slide.shapes.title.text = "Tiêu đề"
        _text_box(slide.shapes, "Thân bài")
        slide.notes_slide.notes_text_frame.text = "Nhấn mạnh phần này khi giảng"

    lines = _lines(read_pptx(_deck(tmp_path, build))[0])

    notes = next(line for line in lines if "Nhấn mạnh phần này khi giảng" in line)
    assert "Thân bài" not in notes
    assert lines.index(notes) > lines.index(next(line for line in lines if "Thân bài" in line))


def test_a_slide_without_any_text_gives_no_unit_and_the_others_keep_their_numbers(tmp_path):
    def build(prs):
        first = prs.slides.add_slide(prs.slide_layouts[BLANK])
        _text_box(first.shapes, "Slide một")
        prs.slides.add_slide(prs.slide_layouts[BLANK])
        third = prs.slides.add_slide(prs.slide_layouts[BLANK])
        _text_box(third.shapes, "Slide ba")

    units = read_pptx(_deck(tmp_path, build))

    assert [u.locator["slide_index"] for u in units] == [1, 3]


def test_a_line_break_inside_a_paragraph_becomes_a_space(tmp_path):
    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[BLANK])
        _text_box(slide.shapes, "dòng một\vdòng hai")

    text = read_pptx(_deck(tmp_path, build))[0].text

    assert "dòng một dòng hai" in text
    assert "\x0b" not in text


def test_read_document_gives_deck_units_the_course_and_the_source_name(tmp_path):
    def build(prs):
        _text_box(prs.slides.add_slide(prs.slide_layouts[BLANK]).shapes, "Nội dung")

    units = read_document(_deck(tmp_path, build), course_id="course-1", source_id="bai-giang.pptx")

    assert [(u.course_id, u.source_id) for u in units] == [("course-1", "bai-giang.pptx")]


def test_an_arrow_typed_in_wingdings_is_read_as_an_arrow(tmp_path):
    # As in the course's Chapter1.pptx: "1848-1851, ⇒ phát triển". The arrow is a private-use code that only
    # Wingdings draws, so it reached the model as an empty box and the "leads to" was lost.
    from lxml import etree
    from pptx.oxml.ns import qn

    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[BLANK])
        paragraph = _text_box(slide.shapes, "1848-1851, ").text_frame.paragraphs[0]
        for text, font in (("", "Wingdings"), (" phát triển lý luận", None)):
            run = paragraph.add_run()
            run.text = text
            if font:
                etree.SubElement(run._r.get_or_add_rPr(), qn("a:sym"), typeface=font)

    (unit,) = read_pptx(_deck(tmp_path, build))
    assert _lines(unit) == ["1848-1851, → phát triển lý luận"]
