"""Publishing a module to Moodle: the content goes in as a Book of short chapters (learning
objective, content, one chapter per slide, example), then the practice quiz, then the quiz.
The explanation is not in the Book, where it would give the answer away before the quiz: it is
the feedback the learner sees after answering. Moodle itself is replaced by a recorder, so the
tests read what would have been sent to it."""

import random
import uuid

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import (
    ComponentType,
    Course,
    Document,
    Module,
    ModuleComponent,
    Question,
    User,
    UserRole,
)
from app.moodle import client as moodle_client

client = TestClient(app)

EXPLANATION = "Vì sao B đúng và ba phương án còn lại sai: EXPLANATION-MARKER."
EXPLANATION_2 = "Lời giải của câu thứ hai: EXPLANATION-2-MARKER."
QUIZ_STEM = "Encapsulation là gì? QUIZ-STEM-MARKER"
QUIZ_STEM_2 = "Vì sao dùng private? QUIZ-STEM-2-MARKER"


@pytest.fixture
def moodle(monkeypatch):
    """Records every request the backend makes of Moodle, in order."""
    calls: list[tuple[str, dict]] = []

    def create_book(course_id, name, chapters, section_num=0):
        calls.append(("book", {"course_id": course_id, "name": name, "chapters": chapters, "section": section_num}))
        return 101

    def create_quiz(course_id, name, questions, section_num=0, behaviour="deferredfeedback"):
        calls.append(("quiz", {
            "course_id": course_id, "name": name, "questions": questions, "section": section_num,
            "behaviour": behaviour,
        }))
        return 200 + len(calls)

    monkeypatch.setattr(moodle_client, "create_book", create_book)
    monkeypatch.setattr(moodle_client, "create_quiz", create_quiz)
    return calls


class Published:
    """A module in a course that is linked to Moodle, and the way to publish it."""

    def __init__(self):
        suffix = uuid.uuid4().hex[:8]
        self.moodle_course_id = random.randint(10**6, 10**9)
        db = SessionLocal()
        try:
            user = User(email=f"book-{suffix}@example.com", name="T", hashed_password="x", role=UserRole.teacher)
            db.add(user)
            db.flush()
            course = Course(name=f"Course {suffix}", owner_id=user.id, moodle_course_id=self.moodle_course_id)
            db.add(course)
            db.flush()
            document = Document(course_id=course.id, filename="x.pdf")
            db.add(document)
            db.flush()
            self.user_id, self.course_id, self.document_id = user.id, course.id, document.id
            db.commit()
        finally:
            db.close()
        self.headers = {"Authorization": f"Bearer {create_access_token(self.user_id)}"}

    def module(self, *, components=(), **fields) -> uuid.UUID:
        db = SessionLocal()
        try:
            module = Module(document_id=self.document_id, course_id=self.course_id, title="Encapsulation", **fields)
            db.add(module)
            db.flush()
            for type_, content, questions in components:
                component = ModuleComponent(module_id=module.id, type=type_, content=content)
                db.add(component)
                db.flush()
                for position, (stem, explanation) in enumerate(questions):
                    db.add(Question(
                        component_id=component.id, position=position, stem=stem,
                        choices={"options": ["A", "B", "C", "D"]}, answer_key="1", explanation=explanation,
                    ))
            db.commit()
            return module.id
        finally:
            db.close()

    def publish(self, module_id, section_num=0):
        return client.post(
            f"/modules/{module_id}/publish-to-moodle", json={"section_num": section_num}, headers=self.headers
        )

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(Course).filter(Course.id == self.course_id).delete(synchronize_session=False)
            db.query(User).filter(User.id == self.user_id).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


@pytest.fixture
def published():
    p = Published()
    yield p
    p.cleanup()


def full_module(p: Published, **overrides) -> uuid.UUID:
    fields = {
        "learning_objective": "Sau khi hoàn thành module, người học có thể phân tích Encapsulation.",
        "bloom_level": "analyze",
        "sub_objectives": ["Mô tả cách gộp dữ liệu vào lớp", "So sánh public và private"],
        **overrides,
    }
    return p.module(
        components=[
            (ComponentType.content, {"markdown": "## Định nghĩa\n\n- Gộp dữ liệu và phương thức"}, []),
            (
                ComponentType.slides,
                {"slides": [
                    {"title": "Khái niệm", "bullets": ["Đóng gói dữ liệu", "Che giấu chi tiết"]},
                    {"title": "Lợi ích", "bullets": ["Bảo vệ toàn vẹn dữ liệu"]},
                ]},
                [],
            ),
            (ComponentType.example, {"examples": [
                {"context": "Tài khoản ngân hàng", "analysis": "Số dư là private"},
                {"context": "Hồ sơ sinh viên", "analysis": "Điểm chỉ sửa qua phương thức"},
            ]}, []),
            (
                ComponentType.quiz, {"questions": [{"passed": True}, {"passed": True}]},
                [(QUIZ_STEM, "Giải thích ngắn của câu hỏi quiz"), (QUIZ_STEM_2, "Giải thích ngắn của câu hai")],
            ),
            (ComponentType.practice, {"questions": [{"passed": True}]},
             [("Câu luyện tập PRACTICE-STEM", "Giải thích của câu luyện tập")]),
            (ComponentType.explanation, {"items": [
                {"component": "quiz", "position": 0, "reasoning": "r", "explanation_text": EXPLANATION},
                {"component": "quiz", "position": 1, "reasoning": "r", "explanation_text": EXPLANATION_2},
            ]}, []),
        ],
        **fields,
    )


def _book(calls):
    return next(args for kind, args in calls if kind == "book")


def _quizzes(calls):
    return [args for kind, args in calls if kind == "quiz"]


def _text(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(" ")


def test_a_module_is_published_as_a_book_then_the_practice_quiz_then_the_quiz(published, moodle):
    response = published.publish(full_module(published), section_num=3)

    assert response.status_code == 200
    assert [kind for kind, _ in moodle] == ["book", "quiz", "quiz"]
    assert [args["name"] for _, args in moodle] == [
        "Encapsulation", "Encapsulation - Practice", "Encapsulation - Quiz",
    ]
    assert {args["course_id"] for _, args in moodle} == {published.moodle_course_id}
    assert {args["section"] for _, args in moodle} == {3}


def test_the_response_names_each_activity_that_was_created(published, moodle):
    body = published.publish(full_module(published)).json()

    assert body["moodle_course_id"] == published.moodle_course_id
    assert body["book_cmid"] == 101
    assert {"quiz_cmid", "practice_cmid"} <= set(body)
    assert "page_cmid" not in body


def test_the_book_has_a_chapter_for_the_objective_the_content_the_slides_and_the_example(published, moodle):
    published.publish(full_module(published))

    chapters = _book(moodle)["chapters"]

    top_level = [c["title"] for c in chapters if c["subchapter"] == 0]
    assert top_level == ["Mục tiêu học tập", "Nội dung", "Slide", "Ví dụ"]


def test_the_objective_chapter_has_the_objective_the_sub_objectives_and_the_bloom_level(published, moodle):
    published.publish(full_module(published))

    objective = _text(_book(moodle)["chapters"][0]["content"])

    assert "phân tích Encapsulation" in objective
    assert "Mô tả cách gộp dữ liệu vào lớp" in objective
    assert "So sánh public và private" in objective
    assert "Phân tích" in objective  # the Bloom level, in the learner's language


def test_every_slide_is_a_short_sub_chapter_of_the_slide_chapter(published, moodle):
    published.publish(full_module(published))

    chapters = _book(moodle)["chapters"]

    titles = [c["title"] for c in chapters]
    slide_at = titles.index("Slide")
    subchapters = chapters[slide_at + 1 : slide_at + 3]
    assert [c["title"] for c in subchapters] == ["Khái niệm", "Lợi ích"]
    assert all(c["subchapter"] == 1 for c in subchapters)
    assert "Đóng gói dữ liệu" in _text(subchapters[0]["content"])
    assert "Che giấu chi tiết" in _text(subchapters[0]["content"])
    assert "Bảo vệ toàn vẹn dữ liệu" in _text(subchapters[1]["content"])
    assert chapters[slide_at + 3]["title"] == "Ví dụ"


def test_the_content_and_every_example_are_in_their_chapters(published, moodle):
    published.publish(full_module(published))

    by_title = {c["title"]: _text(c["content"]) for c in _book(moodle)["chapters"]}

    assert "Gộp dữ liệu và phương thức" in by_title["Nội dung"]
    for text in ("Tài khoản ngân hàng", "Số dư là private", "Hồ sơ sinh viên", "Điểm chỉ sửa qua phương thức"):
        assert text in by_title["Ví dụ"]


def test_chapter_fields_are_what_moodle_expects(published, moodle):
    published.publish(full_module(published))

    for chapter in _book(moodle)["chapters"]:
        assert set(chapter) == {"title", "content", "subchapter"}
        assert chapter["title"].strip() and chapter["content"].strip()
        # Moodle parses this as an integer: True would arrive as the text "True".
        assert type(chapter["subchapter"]) is int


def test_the_explanation_is_not_in_the_book_but_is_the_feedback_of_the_quiz_question(published, moodle):
    published.publish(full_module(published))

    book_text = " ".join(_text(c["content"]) for c in _book(moodle)["chapters"])
    assert "EXPLANATION-MARKER" not in book_text

    quiz = next(q for q in _quizzes(moodle) if q["name"].endswith("- Quiz"))
    assert "EXPLANATION-MARKER" in quiz["questions"][0]["explanation"]


def test_every_quiz_question_gets_the_explanation_written_for_it_in_order(published, moodle):
    """Các câu của một quiz cùng được tạo trong một transaction: nếu thứ tự không được giữ bằng vị
    trí, lời giải của câu này sẽ nằm dưới câu kia."""
    published.publish(full_module(published))

    quiz = next(q for q in _quizzes(moodle) if q["name"].endswith("- Quiz"))

    assert [q["stem"] for q in quiz["questions"]] == [QUIZ_STEM, QUIZ_STEM_2]
    assert "EXPLANATION-MARKER" in quiz["questions"][0]["explanation"]
    assert "EXPLANATION-2-MARKER" in quiz["questions"][1]["explanation"]


def test_the_practice_question_keeps_its_own_explanation(published, moodle):
    published.publish(full_module(published))

    practice = next(q for q in _quizzes(moodle) if q["name"].endswith("- Practice"))

    assert practice["questions"][0]["explanation"] == "Giải thích của câu luyện tập"
    assert practice["questions"][0]["correct_index"] == 1


def test_a_quiz_without_a_generated_explanation_keeps_the_question_explanation(published, moodle):
    module_id = published.module(
        learning_objective="Mục tiêu",
        components=[
            (ComponentType.content, {"markdown": "Nội dung"}, []),
            (ComponentType.quiz, {"questions": [{"passed": True}]}, [("Câu hỏi", "Giải thích của chính câu hỏi")]),
        ],
    )

    published.publish(module_id)

    quiz = _quizzes(moodle)[0]
    assert quiz["questions"][0]["explanation"] == "Giải thích của chính câu hỏi"


def test_a_module_with_only_some_components_gets_only_those_chapters(published, moodle):
    module_id = published.module(
        learning_objective="Mục tiêu duy nhất", components=[(ComponentType.content, {"markdown": "Chỉ có nội dung"}, [])]
    )

    response = published.publish(module_id)

    assert response.status_code == 200
    assert [c["title"] for c in _book(moodle)["chapters"]] == ["Mục tiêu học tập", "Nội dung"]
    assert _quizzes(moodle) == []
    assert {"quiz_cmid", "practice_cmid"}.isdisjoint(response.json())


def test_a_module_with_nothing_to_publish_is_refused_before_moodle_is_asked(published, moodle):
    response = published.publish(published.module())

    assert response.status_code == 422
    assert moodle == []


@pytest.mark.parametrize(
    "hostile",
    [
        "<script>alert(1)</script>",
        '<img src=x onerror="alert(1)">',
        '<a href="javascript:alert(1)" onclick="x()">link</a>',
    ],
)
def test_nothing_active_from_the_source_reaches_moodle(published, moodle, hostile):
    module_id = published.module(
        learning_objective=f"Mục tiêu {hostile}",
        sub_objectives=[f"Con {hostile}"],
        components=[
            (ComponentType.content, {"markdown": f"## Tiêu đề\n\n{hostile}\n\n- mục {hostile}"}, []),
            (ComponentType.slides, {"slides": [{"title": f"Slide {hostile}", "bullets": [f"ý {hostile}", "ý thường"]}]}, []),
            (ComponentType.example, {"examples": [{"context": f"Bối cảnh {hostile}", "analysis": f"Phân tích {hostile}"}]}, []),
            (ComponentType.quiz, {"questions": [{"passed": True}]}, [(f"Hỏi {hostile}", f"Giải thích {hostile}")]),
            (ComponentType.explanation, {"items": [
                {"component": "quiz", "position": 0, "reasoning": "r", "explanation_text": f"Đáp {hostile}"},
            ]}, []),
        ],
    )

    published.publish(module_id)

    html = [c["content"] for c in _book(moodle)["chapters"]]
    for quiz in _quizzes(moodle):
        for question in quiz["questions"]:
            html += [question["stem"], question["explanation"], *question["choices"]]
    for fragment in html:
        soup = BeautifulSoup(fragment, "html.parser")
        assert soup.find(["script", "img", "iframe"]) is None
        assert not soup.find(attrs=lambda attrs: attrs and any(k.lower().startswith("on") for k in attrs))
        assert not any(a.get("href", "").lower().startswith("javascript:") for a in soup.find_all("a"))
    for chapter in _book(moodle)["chapters"]:
        assert "<" not in chapter["title"]


def test_the_book_is_named_after_the_module_and_a_tag_in_the_title_is_not_passed_on(published, moodle):
    module_id = published.module(
        learning_objective="Mục tiêu", components=[(ComponentType.content, {"markdown": "x"}, [])]
    )
    db = SessionLocal()
    try:
        db.query(Module).filter(Module.id == module_id).update({"title": "Bài <b>1</b>"})
        db.commit()
    finally:
        db.close()

    published.publish(module_id)

    assert "<" not in _book(moodle)["name"]
    assert "Bài" in _book(moodle)["name"]


def test_a_moodle_failure_is_reported_as_a_bad_gateway(published, monkeypatch):
    def failing_book(course_id, name, chapters, section_num=0):
        raise moodle_client.MoodleError("local_lms_bridge_create_book: no such course")

    monkeypatch.setattr(moodle_client, "create_book", failing_book)

    response = published.publish(full_module(published))

    assert response.status_code == 502


def test_the_practice_gets_immediate_feedback_and_the_quiz_keeps_deferred_feedback(published, moodle):
    published.publish(full_module(published))

    behaviour = {q["name"].rsplit(" - ", 1)[1]: q["behaviour"] for q in _quizzes(moodle)}

    assert behaviour == {"Practice": "immediatefeedback", "Quiz": "deferredfeedback"}


@pytest.mark.parametrize("kind", ["quiz", "practice"])
def test_question_edits_keep_ai_original_and_publish_the_latest_version(published, moodle, kind):
    module_id = full_module(published)
    detail = client.get(f"/modules/{module_id}", headers=published.headers).json()
    component = next(c for c in detail["components"] if c["type"] == kind)
    question = component["questions"][0]
    url = f'/modules/{module_id}/questions/{question["id"]}'
    for stem in ["EDIT-ONE", "EDIT-TWO"]:
        response = client.patch(url, headers=published.headers, json={
            "stem": stem, "choices": ["New A", "New B"], "answer_key": 0, "explanation": "EDIT-EXPLANATION",
        })
        assert response.status_code == 200
    with SessionLocal() as db:
        stored = db.get(ModuleComponent, component["id"])
        original = stored.content["ai_original"]
        assert original["content"] == component["content"]
        assert original["questions"][0]["stem"] == question["stem"]
        assert original["questions"][0]["choices"] == question["choices"]
        assert original["questions"][0]["answer_key"] == question["answer_key"]
        assert original["questions"][0]["explanation"] == question["explanation"]
    # An existing whole-component editor cannot remove or forge the snapshot.
    response = client.patch(f'/modules/{module_id}/components/{component["id"]}',
                            headers=published.headers, json={"content": {"ai_original": {"forged": True}}})
    assert response.status_code == 200
    assert response.json()["content"]["ai_original"] == original
    assert published.publish(module_id).status_code == 200
    quiz = next(q for q in _quizzes(moodle) if q["name"].endswith(f'- {kind.title()}'))
    edited = quiz["questions"][0]
    assert edited["stem"] == "EDIT-TWO"
    assert edited["choices"] == ["New A", "New B"]
    assert edited["correct_index"] == 0
    assert edited["explanation"] == "EDIT-EXPLANATION"
    assert "ai_original" not in str(moodle)
    assert question["stem"] not in str(moodle)


def test_question_edit_by_non_owner_returns_404(published):
    module_id = full_module(published)
    detail = client.get(f"/modules/{module_id}", headers=published.headers).json()
    question = next(c for c in detail["components"] if c["type"] == "quiz")["questions"][0]
    other = Published()
    try:
        response = client.patch(f'/modules/{module_id}/questions/{question["id"]}', headers=other.headers,
                                json={"stem": "x", "choices": ["A", "B"], "answer_key": 0, "explanation": "e"})
        assert response.status_code == 404
    finally:
        other.cleanup()
