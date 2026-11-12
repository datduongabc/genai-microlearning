"""Slides are HTML with KaTeX, captured by a headless Chromium. These tests render for real."""

import io

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
    shot = renderer.capture(scene, None, "Nguồn: Chapter 5.pdf, trang 9-10")
    assert shot.png.startswith(b"\x89PNG")
    assert _size(shot.png) == (1280, 720)
    # KaTeX replaced the source with its markup; the LaTeX is left only in the annotation KaTeX
    # keeps for screen readers, never as visible text.
    assert 'class="katex-display"' in shot.dom
    assert "\\frac" not in shot.visible_text
    assert "Thời gian chờ trung bình của FCFS" in shot.visible_text
    assert shot.tex_error is None


def test_render_slide_returns_png_bytes_of_the_asked_size():
    png = render_slide(_scene(), None, "", size=(1920, 1080))
    assert _size(png) == (1920, 1080)


def test_subscripts_render_as_sub():
    page = slide_html(_scene(title="Tiến trình P_1", bullets=["P_1 chạy trước P_{2}", "time_quantum giữ nguyên"]),
                      None, "")
    assert "P<sub>1</sub>" in page
    assert "P<sub>2</sub>" in page
    # A word joined by an underscore is not a subscript.
    assert "time_quantum" in page


def test_text_from_the_script_is_escaped():
    page = slide_html(_scene(title="<script>alert(1)</script>", bullets=["a < b & c", "x"]), None, "")
    assert "<script>alert(1)" not in page
    assert "a &lt; b &amp; c" in page


def test_inline_math_in_a_bullet_is_rendered(renderer):
    shot = renderer.capture(_scene(bullets=[r"Trung bình là $\frac{a}{b}$", "Ý hai"]), None, "")
    assert 'class="katex"' in shot.dom
    assert "\\frac" not in shot.visible_text


def test_a_scene_without_an_image_uses_the_text_layout(renderer):
    scene = _scene(kind="illustration", image_prompt="A queue of P1, P2, P3")
    text_only = renderer.capture(scene, None, "")
    with_image = renderer.capture(scene, _picture(), "")
    assert text_only.layout == "text-only" and with_image.layout == "with-image"
    assert "<img" not in text_only.dom and "<img" in with_image.dom
    # Without a picture the text column takes the slide's width; with one, it leaves the right side.
    assert text_only.text_width > 1000
    assert with_image.text_width < 700
    right = Image.open(io.BytesIO(with_image.png)).convert("RGB").crop((800, 200, 1200, 500))
    assert (23, 182, 214) in {c for _, c in right.getcolors(1 << 16)}


def test_code_and_table_scenes_render(renderer):
    code = renderer.capture(_scene(kind="code", code="while (true) {\n  // chờ CPU\n}"), None, "")
    assert "chờ CPU" in code.visible_text
    table = renderer.capture(_scene(kind="table", table=[["Tiến trình", "Burst"], ["P_1", "24"], ["P_2", "3"]]),
                             None, "")
    assert "<table>" in table.dom and "P<sub>1</sub>" in table.dom


def test_long_text_is_shrunk_to_fit_the_slide(renderer):
    long = "Một ý rất dài về lập lịch CPU giải thích từng bước của thuật toán Round Robin với lượng tử thời gian " * 2
    shot = renderer.capture(_scene(title="Tiêu đề " * 8, bullets=[long] * 4), None, "Nguồn: x.pdf")
    assert not shot.overflows
    assert shot.scale < 1


def test_a_broken_formula_is_shown_not_left_hanging(renderer):
    shot = renderer.capture(_scene(kind="formula", formula=r"\frac{1}{"), None, "")
    assert shot.tex_error
    assert _size(shot.png) == (1280, 720)


def test_no_network_is_needed_to_render(renderer):
    """The page's every request outside the renderer's own origin is aborted, and a slide still
    gets its fonts and its formula: KaTeX and the fonts are bundled."""
    shot = renderer.capture(_scene(kind="formula", formula=r"\frac{a}{b}"), _picture(), "Nguồn: x.pdf")
    assert shot.blocked == []
    assert shot.fonts_loaded == {"Be Vietnam Pro 400": True, "Be Vietnam Pro 700": True, "KaTeX_Main": True}


def test_a_request_to_the_network_is_blocked(renderer):
    page = ('<!doctype html><html><body><img src="https://example.com/x.png">'
            "<script>document.body.dataset.ready = 1</script></body></html>")
    shot = renderer.capture_html(page)
    assert shot.blocked == ["https://example.com/x.png"]
