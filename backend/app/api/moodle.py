import html
import uuid

import bleach
import markdown
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.generation.module_generation_pipeline import is_control
from app.models import ComponentType, Course, Module, ModuleComponent, User
from app.moodle import client as moodle_client
from app.moodle.accounts import enrol_linked_teacher, require_moodle_user_id
from app.moodle.errors import MOODLE_FAILURES, moodle_failure

router = APIRouter(prefix="/modules", tags=["moodle"], dependencies=[Depends(get_current_user)])

# LLM output is untrusted content (a source document could carry a prompt
# injection payload trying to get a <script>/onerror= into generated text) --
# strip anything outside this allowlist before it's stored as raw HTML in
# Moodle (a Book chapter is FORMAT_HTML and quiz text is PARAM_RAW, both rendered unescaped).
_ALLOWED_TAGS = ["h1", "h2", "h3", "p", "ul", "ol", "li", "strong", "em", "b", "i", "br", "code", "pre", "blockquote"]


def _clean_html(html_text: str) -> str:
    return bleach.clean(html_text, tags=_ALLOWED_TAGS, attributes={}, strip=True)


def _plain(text: str) -> str:
    """Text for a name or a chapter title, which Moodle shows as plain text: no tag survives."""
    return " ".join(bleach.clean(text or "", tags=[], strip=True).split())


def _component(module: Module, component_type: ComponentType) -> ModuleComponent | None:
    return next((c for c in module.components if c.type == component_type), None)


def _list(items: list[str], *, ordered: bool = False) -> str:
    tag = "ol" if ordered else "ul"
    return f"<{tag}>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + f"</{tag}>"


def _objective_html(module: Module) -> str:
    parts = [f"<p>{html.escape(module.learning_objective or '')}</p>"]
    if module.sub_objectives:
        parts.append("<p><strong>Sau bài này bạn sẽ:</strong></p>")
        parts.append(_list([str(s) for s in module.sub_objectives]))
    return "".join(parts)


def _build_book_chapters(module: Module) -> list[dict]:
    """The module as short Book chapters: objective, content, one sub-chapter per slide under a
    "Slide" chapter, the examples. The explanations are left out on purpose: each explains the
    answer of one question, so it belongs to that question's feedback, not to the reading before it."""
    chapters: list[dict] = []

    def add(title: str, body: str, fallback_title: str, subchapter: int = 0) -> None:
        chapters.append(
            {"title": _plain(title) or fallback_title, "content": _clean_html(body), "subchapter": subchapter}
        )

    if module.learning_objective:
        add("Mục tiêu học tập", _objective_html(module), "Mục tiêu học tập")

    content = _component(module, ComponentType.content)
    text = (content.content.get("markdown") or "") if content else ""
    if text.strip():
        add("Nội dung", markdown.markdown(text), "Nội dung")

    slides_component = _component(module, ComponentType.slides)
    slides = (slides_component.content.get("slides") or []) if slides_component else []
    if slides:
        titles = [_plain(s.get("title", "")) or f"Slide {n}" for n, s in enumerate(slides, start=1)]
        add("Slide", _list(titles, ordered=True), "Slide")
        for n, (title, slide) in enumerate(zip(titles, slides, strict=True), start=1):
            add(title, _list([str(b) for b in slide.get("bullets", []) if str(b).strip()]), f"Slide {n}", 1)

    example = _component(module, ComponentType.example)
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


def _explanations(module: Module, component_type: ComponentType) -> dict[int, str]:
    """Lời giải thích của từng câu theo vị trí, lấy từ thành phần Explanation: đó là bản giảng viên
    đã sửa, còn Question.explanation là bản lúc sinh."""
    component = _component(module, ComponentType.explanation)
    items = (component.content.get("items") or []) if component else []
    return {
        int(item["position"]): str(item.get("explanation_text") or "").strip()
        for item in items
        if item.get("component") == component_type.value and "position" in item
    }


def _build_questions(component: ModuleComponent, explanations: dict[int, str] | None = None) -> list[dict]:
    """The questions as Moodle wants them, each with the explanation the learner sees after
    answering: the teacher's edited one when there is one, otherwise the one it was made with."""
    explanations = explanations or {}
    return [
        {
            "stem": _clean_html(q.stem),
            "choices": [_clean_html(c) for c in q.choices.get("options", [])],
            "correct_index": int(q.answer_key),
            "explanation": _clean_html(explanations.get(q.position) or q.explanation or ""),
        }
        for q in component.questions
    ]


class PublishRequest(BaseModel):
    # Which existing Moodle section to add the Book/Quiz into (0 = the
    # course's general/top section) -- see /courses/{id}/moodle-sections for
    # the list a teacher picks from. Defaults to 0 for backwards
    # compatibility with older frontend builds that don't send it yet.
    section_num: int = Field(default=0, ge=0, le=1000)


@router.post("/{module_id}/publish-to-moodle")
@limiter.limit("20/minute", key_func=user_or_address)
def publish_to_moodle(
    request: Request,
    module_id: uuid.UUID,
    payload: PublishRequest = PublishRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    module = db.get(Module, module_id)
    if module is None or module.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Module not found")

    if is_control(db, module.id):
        raise HTTPException(
            status_code=422,
            detail="Mô-đun đối chứng (không RAG) chỉ dùng cho nghiên cứu, không được đẩy lên Moodle.",
        )

    course = db.get(Course, module.course_id)
    if course.moodle_course_id is None:
        raise HTTPException(
            status_code=400,
            detail="Course is not linked to a Moodle course yet -- link or create one first "
            "(POST /courses/{id}/link-moodle or /courses/{id}/create-moodle)",
        )

    chapters = _build_book_chapters(module)
    if not chapters:
        raise HTTPException(status_code=422, detail="Mô-đun chưa có nội dung nào để đẩy lên Moodle.")

    # The lecturer must have linked their Moodle account to see what is pushed, so nothing is
    # written for one who has not.
    require_moodle_user_id(current_user)
    try:
        # Idempotent: the lecturer teaches the course they push to, however it was linked.
        enrol_linked_teacher(db, current_user, course.moodle_course_id)
        result = {
            "moodle_course_id": course.moodle_course_id,
            "book_cmid": moodle_client.create_book(
                course.moodle_course_id, _plain(module.title) or "Mô-đun", chapters, payload.section_num
            ),
        }

        # Practice comes before the quiz: it is the rehearsal.
        practice = _component(module, ComponentType.practice)
        if practice and practice.questions:
            result["practice_cmid"] = moodle_client.create_quiz(
                course.moodle_course_id, f"{_plain(module.title)} - Practice",
                _build_questions(practice, _explanations(module, ComponentType.practice)), payload.section_num,
                behaviour="immediatefeedback",
            )

        quiz = _component(module, ComponentType.quiz)
        if quiz and quiz.questions:
            result["quiz_cmid"] = moodle_client.create_quiz(
                course.moodle_course_id, f"{_plain(module.title)} - Quiz",
                _build_questions(quiz, _explanations(module, ComponentType.quiz)), payload.section_num,
                behaviour="deferredfeedback",
            )
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db, course) from e

    return result
