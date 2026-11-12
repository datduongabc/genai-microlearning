"""A module as sanitized HTML pieces, shared by every way a module leaves the system: the Moodle
Book and Quiz (app/api/moodle.py) and the SCORM package (app/export/scorm.py)."""

import html

import bleach
import markdown

from app.models import ComponentType, Module, ModuleComponent

# LLM output is untrusted content (a source document could carry a prompt
# injection payload trying to get a <script>/onerror= into generated text) --
# strip anything outside this allowlist before it's stored as raw HTML in
# Moodle (a Book chapter is FORMAT_HTML and quiz text is PARAM_RAW, both rendered unescaped).
_ALLOWED_TAGS = ["h1", "h2", "h3", "p", "ul", "ol", "li", "strong", "em", "b", "i", "br", "code", "pre", "blockquote"]


def clean_html(html_text: str) -> str:
    return bleach.clean(html_text, tags=_ALLOWED_TAGS, attributes={}, strip=True)


def plain(text: str) -> str:
    """Text for a name or a chapter title, which Moodle shows as plain text: no tag survives."""
    return " ".join(bleach.clean(text or "", tags=[], strip=True).split())


def find_component(module: Module, component_type: ComponentType) -> ModuleComponent | None:
    return next((c for c in module.components if c.type == component_type), None)


def html_list(items: list[str], *, ordered: bool = False) -> str:
    tag = "ol" if ordered else "ul"
    return f"<{tag}>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + f"</{tag}>"


def objective_html(module: Module) -> str:
    parts = [f"<p>{html.escape(module.learning_objective or '')}</p>"]
    if module.sub_objectives:
        parts.append("<p><strong>Sau bài này bạn sẽ:</strong></p>")
        parts.append(html_list([str(s) for s in module.sub_objectives]))
    return "".join(parts)


def build_book_chapters(module: Module) -> list[dict]:
    """The module as short Book chapters: objective, content, one sub-chapter per slide under a
    "Slide" chapter, the examples. The explanations are left out on purpose: each explains the
    answer of one question, so it belongs to that question's feedback, not to the reading before it."""
    chapters: list[dict] = []

    def add(title: str, body: str, fallback_title: str, subchapter: int = 0) -> None:
        chapters.append(
            {"title": plain(title) or fallback_title, "content": clean_html(body), "subchapter": subchapter}
        )

    if module.learning_objective:
        add("Mục tiêu học tập", objective_html(module), "Mục tiêu học tập")

    content = find_component(module, ComponentType.content)
    text = (content.content.get("markdown") or "") if content else ""
    if text.strip():
        add("Nội dung", markdown.markdown(text), "Nội dung")

    slides_component = find_component(module, ComponentType.slides)
    slides = (slides_component.content.get("slides") or []) if slides_component else []
    if slides:
        titles = [plain(s.get("title", "")) or f"Slide {n}" for n, s in enumerate(slides, start=1)]
        add("Slide", html_list(titles, ordered=True), "Slide")
        for n, (title, slide) in enumerate(zip(titles, slides, strict=True), start=1):
            add(title, html_list([str(b) for b in slide.get("bullets", []) if str(b).strip()]), f"Slide {n}", 1)

    example = find_component(module, ComponentType.example)
    examples = (example.content.get("examples") or []) if example else []
    if examples:
        add(
            "Ví dụ",
            "".join(
                f"<h4>Ví dụ {n}</h4>"
                f"<p><strong>Bối cảnh:</strong> {html.escape(str(e.get('context', '')))}</p>"
                f"<p><strong>Phân tích:</strong> {html.escape(str(e.get('analysis', '')))}</p>"
                for n, e in enumerate(examples, start=1)
            ),
            "Ví dụ",
        )
    return chapters


def explanations(module: Module, component_type: ComponentType) -> dict[int, str]:
    """Lời giải thích của từng câu theo vị trí, lấy từ thành phần Explanation: đó là bản giảng viên
    đã sửa, còn Question.explanation là bản lúc sinh."""
    component = find_component(module, ComponentType.explanation)
    items = (component.content.get("items") or []) if component else []
    return {
        int(item["position"]): str(item.get("explanation_text") or "").strip()
        for item in items
        if item.get("component") == component_type.value and "position" in item
    }


def build_questions(component: ModuleComponent, explanations: dict[int, str] | None = None) -> list[dict]:
    """The questions as Moodle wants them, each with the explanation the learner sees after
    answering: the teacher's edited one when there is one, otherwise the one it was made with."""
    explanations = explanations or {}
    return [
        {
            "stem": clean_html(q.stem),
            "choices": [clean_html(c) for c in q.choices.get("options", [])],
            "correct_index": int(q.answer_key),
            "explanation": clean_html(explanations.get(q.position) or q.explanation or ""),
        }
        for q in component.questions
    ]


def failing_questions(module: Module) -> list[str]:
    """Quiz/practice questions that failed the quality gate and have not been edited since."""
    failing = []
    for component in module.components:
        if component.type not in (ComponentType.quiz, ComponentType.practice):
            continue
        positions = {q.position for q in component.questions}
        for position, q in enumerate(component.content.get("questions", [])):
            if position in positions and q.get("passed") is False:
                failing.append(f"{component.type.value} {position + 1}: {q.get('fail_reason') or 'không đạt gate chất lượng'}")
    return failing
