"""The slide of a scene: text the system prints, the model's picture beside it, captured as a PNG.

The title, bullets, formula, code and table are printed from the script, so the text
on a slide is always spelled right and matches its source; the image model only draws the picture
(app/video/illustrations.py). The page is HTML set in Be Vietnam Pro, with formulas typeset by
KaTeX, and a headless Chromium takes the screenshot.

Everything the page loads is served by the renderer itself from a made-up origin: the page, the
picture and the bundled assets (app/video/assets, rebuilt by scripts/vendor_slide_assets.sh). Every
other request is aborted, so a slide renders the same with no network, and text from the script
cannot make the worker fetch anything.
"""

import html
import mimetypes
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Self
from urllib.parse import urlsplit

from playwright.sync_api import Route, sync_playwright

from app.generation.video_script import Scene

SIZE = (1280, 720)
ASSETS = Path(__file__).parent / "assets"
_TEMPLATE = Template((Path(__file__).parent / "slide.html").read_text(encoding="utf-8"))
# .invalid never resolves (RFC 2606), so nothing outside the renderer could answer for it.
_ORIGIN = "http://slide.invalid"
_PAGE_PATH = "/slide.html"
_IMAGE_PATH = "/image"
READY_TIMEOUT_MS = 20_000
# /dev/shm is 64 MB in a container, too small for Chromium's shared memory, which then crashes.
_CHROMIUM_ARGS = ["--disable-dev-shm-usage", "--disable-gpu"]


class SlideError(RuntimeError):
    """The page did not finish rendering: a bug in the template or a broken browser, not the script."""


# P_1 and P_{12} are shown as P with a subscript, the way the lecture writes them. Only a single
# letter takes a subscript, so time_quantum stays a word.
_SUBSCRIPT = re.compile(r"\b([A-Za-z])_(?:\{([^{}]+)\}|([A-Za-z0-9]+))")
_INLINE_MATH = re.compile(r"\$([^$\n]+)\$")


def _text(raw: str) -> str:
    return _SUBSCRIPT.sub(lambda m: f"{m[1]}<sub>{m[2] or m[3]}</sub>", html.escape(raw, quote=False))


def _inline(raw: str) -> str:
    """Escaped text with its subscripts, and $...$ left for KaTeX to typeset inline."""
    parts = []
    last = 0
    for m in _INLINE_MATH.finditer(raw):
        parts.append(_text(raw[last:m.start()]))
        parts.append(f'<span data-tex="{html.escape(m[1].strip(), quote=True)}"></span>')
        last = m.end()
    parts.append(_text(raw[last:]))
    return "".join(parts)


def _content(scene: Scene) -> str:
    parts = [f"<h1>{_inline(scene.title)}</h1>"]
    if scene.bullets:
        parts.append("<ul>" + "".join(f"<li>{_inline(b)}</li>" for b in scene.bullets) + "</ul>")
    if scene.formula and scene.formula.strip():
        parts.append(f'<div class="f" data-tex="{html.escape(scene.formula.strip(), quote=True)}"></div>')
    if scene.code and scene.code.strip():
        parts.append(f"<pre><code>{html.escape(scene.code.strip(chr(10)), quote=False)}</code></pre>")
    if scene.table:
        head, *rows = scene.table
        parts.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>" + "".join(
            "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in rows) + "</table>")
    return "\n".join(parts)


def _composed(scene: Scene) -> Scene:
    """Every printed field in NFC: text pasted from a PDF can arrive decomposed (o, circumflex and
    tilde apart), which a font may stack badly and which no longer matches the source it cites."""
    def nfc(text: str | None) -> str | None:
        return unicodedata.normalize("NFC", text) if text is not None else None

    return scene.model_copy(update={
        "title": nfc(scene.title), "bullets": [nfc(b) for b in scene.bullets], "formula": nfc(scene.formula),
        "code": nfc(scene.code), "table": [[nfc(c) for c in row] for row in scene.table] if scene.table else scene.table,
    })


def slide_html(scene: Scene, image: bytes | None, lang: str = "vi") -> str:
    """The page of one slide: its content only, no source line, which the learner does not read on a
    slide. With no picture, the text takes the whole slide."""
    scene = _composed(scene)
    return _TEMPLATE.substitute(
        lang=html.escape(lang, quote=True),
        layout="with-image" if image is not None else "text-only",
        kind=scene.kind,
        content=_content(scene),
        image=f'<div class="img"><img src="{_IMAGE_PATH.lstrip("/")}" alt=""></div>' if image is not None else "",
    )


@dataclass(frozen=True)
class SlideCapture:
    """A rendered slide, and what the tests and the preview check about the page that made it."""

    png: bytes
    dom: str
    visible_text: str
    layout: str
    text_width: float
    scale: float
    overflows: bool
    tex_error: str | None
    code_font: str | None
    fonts_loaded: dict[str, bool]
    blocked: list[str]


def _sniff(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


_INSPECT = """() => ({
  dom: document.documentElement.outerHTML,
  text: document.body.innerText,
  layout: document.body.className,
  textWidth: (document.querySelector('.t') || document.body).getBoundingClientRect().width,
  scale: parseFloat(document.body.dataset.scale || '1'),
  overflows: document.body.dataset.overflows === '1',
  texError: document.body.dataset.texError || null,
  codeFont: document.querySelector('code') ? getComputedStyle(document.querySelector('code')).fontFamily : null,
  fonts: {
    'Be Vietnam Pro 400': document.fonts.check('400 28px "Be Vietnam Pro"', 'Tiến trình'),
    'Be Vietnam Pro 700': document.fonts.check('700 44px "Be Vietnam Pro"', 'Tiến trình'),
    'JetBrains Mono 400': document.fonts.check('400 20px "JetBrains Mono"', 'rỗng'),
    'KaTeX_Main': [...document.fonts].some(f => f.family.replace(/"/g, '') === 'KaTeX_Main' && f.status === 'loaded'),
  },
})"""


class SlideRenderer:
    """One Chromium for many slides: starting it costs about a second, a slide about a tenth.

    Not shared between threads (Playwright's sync API is bound to the thread that started it), so
    each job or request opens its own."""

    def __enter__(self) -> Self:
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(args=_CHROMIUM_ARGS)
        except Exception:
            self._playwright.stop()
            raise
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._browser.close()
        finally:
            self._playwright.stop()

    def capture(self, scene: Scene, image: bytes | None, size: tuple[int, int] = SIZE,
                lang: str = "vi") -> SlideCapture:
        return self.capture_html(slide_html(scene, image, lang), image, size)

    def render(self, scene: Scene, image: bytes | None, size: tuple[int, int] = SIZE,
               lang: str = "vi") -> bytes:
        return self.capture(scene, image, size, lang).png

    def capture_html(self, page_html: str, image: bytes | None = None, size: tuple[int, int] = SIZE) -> SlideCapture:
        width, height = size
        if width * SIZE[1] != height * SIZE[0]:
            raise ValueError(f"a slide is 16:9, not {width}x{height}")
        blocked: list[str] = []
        page_bytes = page_html.encode("utf-8")

        def serve(route: Route) -> None:
            url = route.request.url
            parts = urlsplit(url)
            if f"{parts.scheme}://{parts.netloc}" != _ORIGIN:
                blocked.append(url)
                route.abort("blockedbyclient")
                return
            if parts.path == _PAGE_PATH:
                route.fulfill(body=page_bytes, content_type="text/html; charset=utf-8")
            elif parts.path == _IMAGE_PATH and image is not None:
                route.fulfill(body=image, content_type=_sniff(image))
            else:
                path = (ASSETS / parts.path.removeprefix("/assets/")).resolve()
                if parts.path.startswith("/assets/") and path.is_relative_to(ASSETS.resolve()) and path.is_file():
                    route.fulfill(body=path.read_bytes(),
                                  content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                else:
                    route.fulfill(status=404, body=b"")

        # The page is laid out at 1280x720 CSS pixels and scaled, so a larger PNG is the same slide, sharper.
        context = self._browser.new_context(viewport={"width": SIZE[0], "height": SIZE[1]},
                                            device_scale_factor=width / SIZE[0], java_script_enabled=True)
        try:
            context.route("**/*", serve)
            page = context.new_page()
            page.goto(_ORIGIN + _PAGE_PATH, wait_until="load")
            try:
                page.wait_for_selector("body[data-ready]", state="attached", timeout=READY_TIMEOUT_MS)
            except Exception as exc:
                raise SlideError(f"the slide did not finish rendering: {exc}") from exc
            png = page.screenshot(type="png", full_page=False)
            info = page.evaluate(_INSPECT)
        finally:
            context.close()
        return SlideCapture(
            png=png, dom=info["dom"], visible_text=info["text"], layout=info["layout"],
            text_width=info["textWidth"], scale=info["scale"], overflows=info["overflows"],
            tex_error=info["texError"], code_font=info["codeFont"], fonts_loaded=info["fonts"], blocked=blocked,
        )


def render_slide(scene: Scene, image: bytes | None, size: tuple[int, int] = SIZE,
                 lang: str = "vi") -> bytes:
    """The PNG of one slide. To render several, open one SlideRenderer and call its render.

    Runs in a thread of its own: Playwright's sync API refuses to start in a thread that already
    runs an event loop, which is the case inside an async route or next to another renderer."""
    def render() -> bytes:
        with SlideRenderer() as renderer:
            return renderer.render(scene, image, size, lang)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(render).result()
