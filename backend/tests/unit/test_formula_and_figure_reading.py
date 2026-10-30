"""Công thức và hình phải tới được văn bản của tài liệu: trang PDF còn công thức vỡ được đọc lại từ
ảnh trang qua gateway (chỉ những trang đó, và chỉ nhận bản chép trung thành với lớp chữ), còn chữ
trong hình nội dung của slide và trang PDF được OCR, bỏ qua hình trang trí."""

import io

import pymupdf
import pytest
from PIL import Image
from pptx import Presentation
from pptx.opc.package import Part
from pptx.opc.packuri import PackURI
from pptx.oxml import parse_xml
from pptx.util import Inches

from app.config import settings
from app.ingestion import document_readers, figures, formula_repair
from app.ingestion.document_readers import read_pdf, read_pptx

TITLE_ONLY = 5
_ocr_figure = figures.ocr_figure


def _broken_formula_page(doc, prose):
    """Một trang có câu chữ và một công thức mà dựng lại vẫn còn cặp ngoặc rỗng."""
    page = doc.new_page()
    page.insert_text((50, 60), prose, fontname="helv", fontsize=14)
    page.insert_text((50, 100), "x", fontname="tiit", fontsize=20)
    page.insert_text((80, 100), "=", fontname="symb", fontsize=20)
    page.insert_text((100, 100), "( )", fontname="tiro", fontsize=20)
    return page


def _pdf(tmp_path, build):
    path = tmp_path / "doc.pdf"
    with pymupdf.open() as doc:
        build(doc)
        doc.save(path)
    return str(path)


def _png(size=(400, 300), mode="RGB", color="white"):
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def _no_ocr(monkeypatch):
    # Không test nào ở đây được chạy Tesseract hay gọi gateway thật.
    monkeypatch.setattr(figures, "ocr_figure", lambda blob: "")
    monkeypatch.setattr(formula_repair, "complete_with_image", lambda *a, **k: pytest.fail("unexpected model call"))


# --- công thức PDF ---------------------------------------------------------------


def test_only_pages_with_a_broken_formula_are_re_read_and_a_faithful_transcription_replaces_them(
    tmp_path, monkeypatch,
):
    sent = []

    def model(prompt, png, **kwargs):
        sent.append(prompt)
        return "Gioi han cua day so\n$x = \\left( \\frac{1}{n} \\right)$"

    monkeypatch.setattr(formula_repair, "complete_with_image", model)
    path = _pdf(tmp_path, lambda doc: (
        doc.new_page().insert_text((50, 60), "Trang chi co chu thuong", fontname="helv", fontsize=14),
        _broken_formula_page(doc, "Gioi han cua day so"),
    ))

    units = read_pdf(path)

    assert len(sent) == 1
    assert "Gioi han cua day so" in sent[0]
    second = [u for u in units if u.locator["page"] == 2]
    assert second[0].text == "Gioi han cua day so\n$x = \\left( \\frac{1}{n} \\right)$"
    assert second[0].locator["extraction_method"] == "vision"
    assert all(u.warnings == [] for u in units)


@pytest.mark.parametrize(
    "transcription",
    [
        "Giới hạn của dãy số\n$x = 1$",  # đổi dấu tiếng Việt so với lớp chữ
        "$x = 1$",  # bỏ mất câu chữ
        "Gioi han cua day so\nDay la giai thich ma trang khong he co\n$x = 1$",  # thêm chữ
    ],
)
def test_a_transcription_that_changes_the_prose_is_refused_and_the_page_is_flagged(
    tmp_path, monkeypatch, transcription,
):
    monkeypatch.setattr(formula_repair, "complete_with_image", lambda *a, **k: transcription)

    units = read_pdf(_pdf(tmp_path, lambda doc: _broken_formula_page(doc, "Gioi han cua day so")))

    assert "Gioi han cua day so" in units[0].text
    assert units[0].locator["extraction_method"] == "text_layer"
    assert units[0].warnings == [{"code": "formula_unverified", "page": 1}]


def test_a_failed_model_call_keeps_the_rebuilt_page_with_a_warning(tmp_path, monkeypatch):
    def model(*args, **kwargs):
        raise ConnectionError("gateway down")

    monkeypatch.setattr(formula_repair, "complete_with_image", model)

    units = read_pdf(_pdf(tmp_path, lambda doc: _broken_formula_page(doc, "Gioi han cua day so")))

    assert "Gioi han cua day so" in units[0].text
    assert units[0].warnings == [{"code": "formula_unverified", "page": 1}]


def test_without_a_vision_model_or_past_the_page_limit_no_call_is_made(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "formula_vision_max_pages", 1)
    calls = []
    monkeypatch.setattr(formula_repair, "complete_with_image", lambda prompt, png, **k: calls.append(1) or "x")
    path = _pdf(tmp_path, lambda doc: [_broken_formula_page(doc, f"Trang so {i} cua bai giang") for i in range(3)])

    units = read_pdf(path)
    assert len(calls) == 1
    assert [w for u in units for w in u.warnings] == [{"code": "formula_unverified", "page": p} for p in (1, 2, 3)]

    monkeypatch.setattr(settings, "formula_vision_model", "")
    calls.clear()
    read_pdf(path)
    assert calls == []


def _cmr_prose(page):
    page.insert_text((50, 60), "Ngon ngu nay ke thua tu C va C++) nen", fontname="helv", fontsize=12)
    page.insert_text((50, 80), "giu cu phap cu (xem dinh ly 2.1 o", fontname="helv", fontsize=12)
    page.insert_text((50, 100), "trang truoc) de so sanh.", fontname="helv", fontsize=12)


def _cmr_table(page):
    page.insert_text((50, 60), "Thao tac", fontname="helv", fontsize=12)
    page.insert_text((200, 60), "Y nghia", fontname="helv", fontsize=12)
    page.draw_line((50, 66), (320, 66))
    page.insert_text((50, 80), "push()", fontname="helv", fontsize=12)
    page.insert_text((200, 80), "them vao dinh", fontname="helv", fontsize=12)


@pytest.mark.parametrize("layout", [_cmr_prose, _cmr_table])
def test_latex_body_text_in_computer_modern_roman_is_not_taken_for_a_broken_formula(tmp_path, layout):
    def build(doc):
        page = doc.new_page()
        layout(page)
        for xref, *_ in page.get_fonts():
            doc.xref_set_key(xref, "BaseFont", "/CMR10")

    units = read_pdf(_pdf(tmp_path, build))

    assert ("C++)" if layout is _cmr_prose else "push()") in units[0].text
    assert units[0].warnings == []


def test_latex_commands_for_functions_do_not_count_as_lost_words():
    assert formula_repair._faithful("Ta có $\\lim_{x \\to 0} \\sin x / x = 1$", "Ta có lim sin x/x = 1")


def test_latex_environment_names_are_not_words_added_to_the_page():
    assert formula_repair._faithful(
        "Hệ phương trình $\\begin{cases} x = 1 \\\\ y = 2 \\end{cases}$ có nghiệm",
        "Hệ phương trình { x = 1 y = 2 có nghiệm",
    )


# --- hình ------------------------------------------------------------------------


def test_small_or_repeated_pictures_are_decoration():
    assert figures.is_content_figure(0.3, 1)
    assert not figures.is_content_figure(0.02, 1)
    assert not figures.is_content_figure(0.3, 3)


def test_only_confident_lines_with_words_are_kept_and_a_few_stray_glyphs_are_no_text(monkeypatch):
    def data(image, lang, output_type):
        rows = [  # (word, confidence, line)
            ("Point2D", 95, 1), ("-x:float", 90, 2), ("=", 91, 2), ("0.0f", 92, 2),
            ("+getX():float", 88, 3), ("~“'", 20, 4), ("Gu)", 30, 4),
        ]
        return {
            "text": [r[0] for r in rows], "conf": [r[1] for r in rows],
            "block_num": [1] * len(rows), "par_num": [1] * len(rows), "line_num": [r[2] for r in rows],
        }

    monkeypatch.setattr(figures.pytesseract, "image_to_data", data)
    assert _ocr_figure(_png()) == "Point2D\n-x:float = 0.0f\n+getX():float"

    monkeypatch.setattr(figures.pytesseract, "image_to_data", lambda *a, **k: {
        "text": ["Ab"], "conf": [96], "block_num": [1], "par_num": [1], "line_num": [1],
    })
    assert _ocr_figure(_png()) == ""


def test_a_transparent_diagram_is_read_on_white_and_a_small_one_is_enlarged():
    image = figures._prepare(_png(size=(300, 200), mode="RGBA", color=(0, 0, 0, 0)))
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.width == 1000


def test_figure_text_already_in_the_page_text_is_not_added_twice():
    assert not figures.new_text("Process State Diagram", "Process State Diagram and more")
    assert figures.new_text("new ready running waiting terminated", "Process State")


def test_a_content_image_of_a_pdf_page_adds_its_text_after_the_page_text(tmp_path, monkeypatch):
    monkeypatch.setattr(figures, "ocr_figure", lambda blob: "new ready running waiting")

    def build(doc):
        page = doc.new_page()
        page.insert_text((50, 60), "Diagram of Process State", fontname="helv", fontsize=14)
        page.insert_image(pymupdf.Rect(50, 100, 450, 400), stream=_png())

    units = read_pdf(_pdf(tmp_path, build))

    assert units[0].text == "Diagram of Process State\nHình: new ready running waiting"
    assert units[0].locator["page"] == 1


def test_a_logo_on_every_pdf_page_is_not_read(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(figures, "ocr_figure", lambda blob: seen.append(blob) or "logo text here now")
    logo = _png(color="red")

    def build(doc):
        for i in range(4):
            page = doc.new_page()
            page.insert_text((50, 60), f"Slide {i} has enough text", fontname="helv", fontsize=14)
            page.insert_image(pymupdf.Rect(50, 100, 450, 400), stream=logo)

    units = read_pdf(_pdf(tmp_path, build))

    assert seen == []
    assert all("Hình" not in u.text for u in units)


def test_the_full_page_scan_under_a_searchable_pdf_text_layer_is_not_read(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(figures, "ocr_figure", lambda blob: seen.append(blob) or "new ready running waiting")

    def build(doc):
        page = doc.new_page()
        page.insert_image(page.rect, stream=_png(size=(600, 800)))
        for row in range(12):
            page.insert_text((50, 60 + 20 * row), f"Line {row} of the scanned lecture page text", fontname="helv")

    units = read_pdf(_pdf(tmp_path, build))

    assert seen == []
    assert "Hình" not in units[0].text


# --- PPTX ------------------------------------------------------------------------


def _deck(tmp_path, build):
    prs = Presentation()
    build(prs)
    path = tmp_path / "deck.pptx"
    prs.save(str(path))
    return str(path)


def test_a_class_diagram_picture_reaches_the_slide_text_in_reading_order(tmp_path, monkeypatch):
    monkeypatch.setattr(figures, "ocr_figure", lambda blob: "Point2D\n-x:float = 0.0f\n+getX():float")

    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[TITLE_ONLY])
        slide.shapes.title.text = "Exercise 3 - Point2D and Point3D"
        slide.shapes.add_picture(io.BytesIO(_png()), Inches(1), Inches(1.5), Inches(5), Inches(4))
        box = slide.shapes.add_textbox(Inches(1), Inches(6), Inches(6), Inches(1))
        box.text_frame.text = "Can you write a program based on this class diagram?"

    unit = read_pptx(_deck(tmp_path, build))[0]

    assert unit.text.split("\n") == [
        "Exercise 3 - Point2D and Point3D", "Hình: Point2D", "-x:float = 0.0f", "+getX():float",
        "Can you write a program based on this class diagram?",
    ]
    assert unit.locator == {"slide_index": 1, "slide_title": "Exercise 3 - Point2D and Point3D"}


def test_a_slide_that_is_only_a_picture_of_text_becomes_a_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(figures, "ocr_figure", lambda blob: "KỶ NIỆM\n138 NĂM NGÀY QUỐC TẾ LAO ĐỘNG")

    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        slide.shapes.add_picture(io.BytesIO(_png()), 0, 0, prs.slide_width, prs.slide_height)

    units = read_pptx(_deck(tmp_path, build))

    assert [u.text for u in units] == ["Hình: KỶ NIỆM\n138 NĂM NGÀY QUỐC TẾ LAO ĐỘNG"]
    assert units[0].locator == {"slide_index": 1, "slide_title": ""}


def test_a_small_icon_or_a_picture_on_every_slide_is_not_read(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(figures, "ocr_figure", lambda blob: seen.append(blob) or "")
    logo = _png(color="blue")

    def build(prs):
        for i in range(3):
            slide = prs.slides.add_slide(prs.slide_layouts[TITLE_ONLY])
            slide.shapes.title.text = f"Slide {i}"
            slide.shapes.add_picture(io.BytesIO(logo), Inches(1), Inches(2), Inches(5), Inches(4))
            slide.shapes.add_picture(io.BytesIO(_png(color=(i, 0, 0))), 0, 0, Inches(0.5), Inches(0.5))

    read_pptx(_deck(tmp_path, build))

    assert seen == []


_SMARTART_DATA = """<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram"
    xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><dgm:ptLst>
  <dgm:pt modelId="1"><dgm:t><a:p><a:r><a:t>có tính tổ chức kỷ luật cao</a:t></a:r></a:p></dgm:t></dgm:pt>
  <dgm:pt modelId="2"><dgm:t><a:p><a:r><a:t>mang bản chất quốc tê</a:t></a:r><a:r><a:t>́</a:t></a:r></a:p></dgm:t></dgm:pt>
</dgm:ptLst></dgm:dataModel>"""

_SMARTART_FRAME = """<p:graphicFrame xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
    xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:nvGraphicFramePr><p:cNvPr id="9" name="Diagram 1"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>
  <p:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></p:xfrm>
  <a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram">
    <dgm:relIds xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" r:dm="{rid}" r:lo="" r:qs="" r:cs=""/>
  </a:graphicData></a:graphic>
</p:graphicFrame>"""


def test_the_text_of_a_smartart_diagram_is_read_from_its_data_part(tmp_path):
    def build(prs):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        part = Part(PackURI("/ppt/diagrams/data1.xml"),
                    "application/vnd.openxmlformats-officedocument.drawingml.diagramData+xml",
                    prs.part.package, _SMARTART_DATA.encode())
        rid = slide.part.relate_to(
            part, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData")
        slide.shapes._spTree.append(parse_xml(_SMARTART_FRAME.replace("{rid}", rid)))

    units = read_pptx(_deck(tmp_path, build))

    assert units[0].text == "có tính tổ chức kỷ luật cao\nmang bản chất quốc tế"


def test_document_readers_reads_figures_through_the_figures_module():
    # read_pdf và read_pptx gọi ocr_figures của module figures, nên fixture trên chặn được mọi OCR.
    assert document_readers.ocr_figures is figures.ocr_figures


def test_a_screenshot_read_with_low_confidence_overall_gives_no_text(monkeypatch):
    rows = [("Screen", 90), ("Shot", 88), ("Folder", 85), ("rà", 20), ("gen", 25), ("cay,534PM", 15), ("ee", 10)]
    monkeypatch.setattr(figures.pytesseract, "image_to_data", lambda *a, **k: {
        "text": [w for w, _ in rows], "conf": [c for _, c in rows],
        "block_num": [1] * len(rows), "par_num": [1] * len(rows), "line_num": list(range(len(rows))),
    })

    assert _ocr_figure(_png()) == ""
