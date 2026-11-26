"""Slides are HTML with KaTeX, captured by a headless Chromium. These tests render for real."""

import io
import re

import pytest
from PIL import Image, ImageDraw

from app.generation.video_script import Scene
from app.video.slides import SlideRenderer, render_slide, slide_html


def _scene(kind="summary", title="Tiêu đề", bullets=("Ý thứ nhất", "Ý thứ hai"), **fields) -> Scene:
    return Scene(kind=kind, title=title, bullets=list(bullets), narration="Một câu.", citations=[], **fields)


def _picture(width=800, height=600) -> bytes:
    im = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(im).rectangle((100, 100, width - 100, height - 100), fill=(23, 182, 214))
    out = io.BytesIO()
    im.save(out, "JPEG")
    return out.getvalue()


@pytest.fixture(scope="module")
def renderer():
    with SlideRenderer() as r:
        yield r


def _size(png: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(png)).size


def test_vietnamese_title_and_formula_render(renderer):
    scene = _scene(kind="formula", title="Thời gian chờ trung bình của FCFS",
                   formula=r"\text{Thời gian chờ trung bình} = \frac{0 + 24 + 27}{3} = 17")
    shot = renderer.capture(scene, None)
    assert shot.png.startswith(b"\x89PNG")
    assert _size(shot.png) == (1280, 720)
    # KaTeX replaced the source with its markup; the LaTeX is left only in the annotation KaTeX
    # keeps for screen readers, never as visible text.
    assert 'class="katex-display"' in shot.dom
    assert "\\frac" not in shot.visible_text
    assert "Thời gian chờ trung bình của FCFS" in shot.visible_text
    assert shot.tex_error is None


def test_render_slide_returns_png_bytes_of_the_asked_size():
    png = render_slide(_scene(), None, size=(1920, 1080))
    assert _size(png) == (1920, 1080)


def test_subscripts_render_as_sub():
    page = slide_html(_scene(title="Tiến trình P_1", bullets=["P_1 chạy trước P_{2}", "time_quantum giữ nguyên"]),
                      None)
    assert "P<sub>1</sub>" in page
    assert "P<sub>2</sub>" in page
    # A word joined by an underscore is not a subscript.
    assert "time_quantum" in page


def test_text_from_the_script_is_escaped():
    page = slide_html(_scene(title="<script>alert(1)</script>", bullets=["a < b & c", "x"]), None)
    assert "<script>alert(1)" not in page
    assert "a &lt; b &amp; c" in page


def test_inline_math_in_a_bullet_is_rendered(renderer):
    shot = renderer.capture(_scene(bullets=[r"Trung bình là $\frac{a}{b}$", "Ý hai"]), None)
    assert 'class="katex"' in shot.dom
    assert "\\frac" not in shot.visible_text


def test_a_scene_without_an_image_uses_the_text_layout(renderer):
    scene = _scene(kind="illustration", image_prompt="A queue of P1, P2, P3")
    text_only = renderer.capture(scene, None)
    with_image = renderer.capture(scene, _picture())
    assert text_only.layout == "text-only" and with_image.layout == "with-image"
    assert "<img" not in text_only.dom and "<img" in with_image.dom
    # Without a picture the text column takes the slide's width; with one, it leaves the right side.
    assert text_only.text_width > 1000
    assert with_image.text_width < 700
    right = Image.open(io.BytesIO(with_image.png)).convert("RGB").crop((800, 200, 1200, 500))
    assert (23, 182, 214) in {c for _, c in right.getcolors(1 << 16)}


def test_code_and_table_scenes_render(renderer):
    code = renderer.capture(_scene(kind="code", code="while (true) {\n  // chờ CPU\n}"), None)
    assert "chờ CPU" in code.visible_text
    # The browser's own `code { font-family: monospace }` must not win over the bundled face, whose
    # Vietnamese glyphs a system monospace font lacks.
    assert code.code_font.startswith('"JetBrains Mono"')
    assert code.fonts_loaded["JetBrains Mono 400"] is True
    table = renderer.capture(_scene(kind="table", table=[["Tiến trình", "Burst"], ["P_1", "24"], ["P_2", "3"]]),
                             None)
    assert "<table>" in table.dom and "P<sub>1</sub>" in table.dom


def test_long_text_is_shrunk_to_fit_the_slide(renderer):
    long = "Một ý rất dài về lập lịch CPU giải thích từng bước của thuật toán Round Robin với lượng tử thời gian " * 2
    shot = renderer.capture(_scene(title="Tiêu đề " * 8, bullets=[long] * 4), None)
    assert not shot.overflows
    assert shot.scale < 1


def test_a_broken_formula_is_shown_not_left_hanging(renderer):
    shot = renderer.capture(_scene(kind="formula", formula=r"\frac{1}{"), None)
    assert shot.tex_error
    assert _size(shot.png) == (1280, 720)


def test_a_diagram_is_drawn_from_its_mermaid_code(renderer):
    """Mermaid draws it in the page, in the slide's font, with every label as written."""
    code = 'flowchart LR\n  A["Đầu vào (x)"] --> B["Xử lý: bước 1"] --> C["Kết quả"]'
    shot = renderer.capture(_scene(kind="diagram", diagram=code), None)
    assert shot.diagram_error is None and not shot.overflows and shot.blocked == []
    assert "<svg" in shot.dom and "Xử lý: bước 1" in shot.visible_text
    assert "Be Vietnam Pro" in shot.dom


def test_a_gantt_of_plain_times_shows_numbers_not_dates(renderer):
    code = "gantt\n  section Máy 1\n  Việc A : 0, 24\n  Việc B : 24, 27\n  Việc C : 27, 30"
    shot = renderer.capture(_scene(kind="diagram", diagram=code), None)
    assert shot.diagram_error is None
    assert "1970" not in shot.visible_text and "Việc B" in shot.visible_text
    # Each task starts where the one before it ends: 24 of 30 units, then 3 and 3.
    bars = re.findall(r'<rect[^>]*class="task[^"]*"[^>]*>', shot.dom)
    spans = [(float(re.search(r' x="([\d.]+)"', b)[1]), float(re.search(r'width="([\d.]+)"', b)[1])) for b in bars]
    (x1, w1), (x2, w2), (x3, w3) = spans
    assert x2 == pytest.approx(x1 + w1, abs=2) and x3 == pytest.approx(x2 + w2, abs=2)
    assert w1 == pytest.approx(8 * w2, rel=0.02) and w2 == pytest.approx(w3, rel=0.02)


def test_a_broken_diagram_is_shown_not_left_hanging(renderer):
    shot = renderer.capture(_scene(kind="diagram", diagram="flowchart LR\n  A[Đầu (vào] --> "), None)
    assert shot.diagram_error
    assert "flowchart LR" in shot.visible_text
    assert _size(shot.png) == (1280, 720)


def test_a_diagram_cannot_run_script(renderer):
    code = 'flowchart LR\n  A["<img src=x onerror=document.body.dataset.pwned=1>"] --> B\n  click A call alert()'
    shot = renderer.capture(_scene(kind="diagram", diagram=code), None)
    # The handler would have set data-pwned on the body; strict mode prints the label as text instead.
    assert "data-pwned" not in shot.dom


def test_a_wide_diagram_is_shrunk_into_the_slide(renderer):
    code = "flowchart LR\n" + "\n".join(f'  N{i}["Bước số {i} của quy trình"] --> N{i + 1}' for i in range(12))
    shot = renderer.capture(_scene(kind="diagram", diagram=code), None)
    assert shot.diagram_error is None and not shot.overflows


def test_no_network_is_needed_to_render(renderer):
    """The page's every request outside the renderer's own origin is aborted, and a slide still
    gets its fonts and its formula: KaTeX and the fonts are bundled."""
    shot = renderer.capture(_scene(kind="formula", formula=r"\frac{a}{b}"), _picture())
    assert shot.blocked == []
    assert {k: v for k, v in shot.fonts_loaded.items() if k != "JetBrains Mono 400"} == {
        "Be Vietnam Pro 400": True, "Be Vietnam Pro 700": True, "KaTeX_Main": True}


def test_a_request_to_the_network_is_blocked(renderer):
    page = ('<!doctype html><html><body><img src="https://example.com/x.png">'
            "<script>document.body.dataset.ready = 1</script></body></html>")
    shot = renderer.capture_html(page)
    assert shot.blocked == ["https://example.com/x.png"]


def test_decomposed_text_is_printed_composed():
    """Text pasted from a PDF can arrive decomposed (o + circumflex + tilde), which some fonts stack
    badly; each field is NFC-normalized before it is printed."""
    import unicodedata

    nfd = unicodedata.normalize("NFD", "rỗng")
    assert nfd != "rỗng"
    page = slide_html(_scene(title=nfd, bullets=[nfd, "b"], code=f"// {nfd}", formula=rf"\text{{{nfd}}}",
                             table=[[nfd], ["x"]]), None)
    assert nfd not in page
    # The template itself names the word once, in a comment.
    assert page.count("rỗng") - slide_html(_scene(), None).count("rỗng") == 5


def test_a_slide_prints_its_content_and_no_source_line():
    page = slide_html(_scene(title="FCFS", bullets=["Đến trước", "Phục vụ trước"]), None)
    assert "FCFS" in page and "Nguồn" not in page and 'class="src"' not in page


def test_a_lit_bullet_changes_only_colours_so_the_slide_keeps_its_layout(renderer):
    scene = _scene(title="FCFS", bullets=["Đến trước phục vụ trước", "Thời gian chờ trung bình dài", "Không ưu tiên"])
    plain, lit = renderer.capture(scene, None), renderer.capture(scene, None, focus=1)
    assert '<ul class="focus">' in lit.dom and lit.dom.count('class="on"') == 1
    assert "Thời gian chờ trung bình dài</li>" in lit.dom.split('class="on">')[1]
    assert (lit.scale, lit.text_width, lit.overflows) == (plain.scale, plain.text_width, plain.overflows)
    assert lit.png != plain.png
