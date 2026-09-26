"""Số lượt gọi mô hình của một module và cờ `self_check` (thí nghiệm có/không tự kiểm).

Chỉ Content và từng câu hỏi còn vòng model tự kiểm; Learning Objective, Slides và Example sinh một
lượt. Mô hình được thay bằng một hàm giả đếm lượt gọi và luôn cho đạt, truy hồi cũng được thay, nên
không tốn hạn mức nào. Phần còn lại của pipeline (Corrective RAG trước Content, vòng sinh-kiểm câu hỏi) chạy thật."""

import importlib
import pkgutil
import uuid
from collections import Counter

import pytest

from app import generation
from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.generation import quiz_graph
from app.generation.content import ContentOutput
from app.generation.example import Example, ExampleOutput
from app.generation.objective import LearningObjective
from app.generation.planner import Concept, PlannerOutput
from app.generation.quiz_graph import QuizQuestion
from app.generation.relevance import RelevanceCheck
from app.generation.slides import Slide, SlidesOutput
from app.models import (
    Chunk,
    ComponentType,
    Course,
    Document,
    DocumentStatus,
    Evaluation,
    ModuleComponent,
    User,
    UserRole,
)

SUB_OBJECTIVES = ["Nêu định nghĩa", "Phân biệt hai trường hợp", "Giải thích vai trò"]

OUTPUTS = {
    PlannerOutput: PlannerOutput(concepts=[Concept(name="Khái niệm", priority=1, summary="tóm tắt")]),
    LearningObjective: LearningObjective(
        bloom_level="understand", verb="giải thích", objective_text="Giải thích khái niệm",
        sub_objectives=SUB_OBJECTIVES,
    ),
    RelevanceCheck: RelevanceCheck(confidence="high", reason="đủ"),
    ContentOutput: ContentOutput(content="## Phần một\n- ý", source_locators=[]),
    SlidesOutput: SlidesOutput(slides=[Slide(title="S1", bullets=["b"]), Slide(title="S2", bullets=["b"])]),
    ExampleOutput: ExampleOutput(examples=[Example(context="bối cảnh", analysis="phân tích")]),
    # Các phương án dài bằng nhau: không vướng bẫy cú pháp, không phải cân bằng lại độ dài đáp án.
    QuizQuestion: QuizQuestion(
        common_misconceptions=["m1", "m2", "m3"], stem="Hỏi", reasoning="r",
        choices=["pa một", "pa hai", "pa ba", "pa bốn"], correct_index=1, explanation="lời giải",
    ),
}


@pytest.fixture
def llm(monkeypatch):
    calls: list[str] = []

    def fake(prompt, response_model, system_instruction=None, **kwargs):
        calls.append(response_model.__name__)
        if response_model in OUTPUTS:
            return OUTPUTS[response_model]
        # Mọi bản kiểm: đạt hết.
        return response_model(**{
            name: True if name.endswith("_pass") else "đạt" for name in response_model.model_fields
        })

    for info in pkgutil.iter_modules(generation.__path__):
        module = importlib.import_module(f"app.generation.{info.name}")
        if hasattr(module, "complete_structured"):
            monkeypatch.setattr(module, "complete_structured", fake)
    return calls


@pytest.fixture
def document(monkeypatch):
    db = SessionLocal()
    try:
        user = User(email=f"sc-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x",
                    role=UserRole.teacher)
        db.add(user)
        db.flush()
        course = Course(name="Self-check", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.pdf", status=DocumentStatus.embedded, raw_text="text")
        db.add(doc)
        db.flush()
        db.add_all(Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=f"đoạn {i}") for i in range(3))
        db.commit()
        ids = {"user": user.id, "course": course.id, "doc": doc.id}
    finally:
        db.close()

    def fake_retrieve(db, course_id, query, **kwargs):
        return db.query(Chunk).filter(Chunk.course_id == course_id).order_by(Chunk.chunk_index).all()

    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(quiz_graph, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "expand_with_parent_context", lambda db, chunks, **kwargs: chunks)

    yield ids

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _generate(document, self_check: bool) -> set[str]:
    db = SessionLocal()
    try:
        module = pipeline.run_module_generation(db, db.get(Document, document["doc"]), self_check=self_check)
        return {e.criterion for e in db.query(Evaluation).filter(Evaluation.module_id == module.id)}
    finally:
        db.close()


def test_with_self_check_one_module_costs_15_calls(document, llm):
    """Ba sub-objective cho ba câu quiz và một câu practice, bốn câu hỏi:

    Planner 1 + Learning Objective 1 + chấm độ liên quan cho Content 1 + Content 1 + kiểm Content 1
    + Slides 1 + Example 1 = 7, cộng mỗi câu hỏi 2 (sinh, kiểm) x 4 = 8: 15. Câu hỏi không còn
    chấm độ liên quan riêng (trước đây thêm 1 lượt mỗi câu, 19 lượt); Corrective RAG chỉ chạy
    một lần, trước Content.

    Tối đa khi mọi bước đều trượt: Planner 1 + LO 1 + chấm độ liên quan 2 (chấm, truy hồi lại, chấm
    lại) + Content 2 x 2 (sinh, kiểm, hai lần) + Slides 1 + Example 1 = 10, cộng bốn câu x 4 (sinh,
    kiểm, hai lần) = 16, cộng sinh lại một câu vì lộ đáp án qua độ dài 4: 30."""
    criteria = _generate(document, self_check=True)

    assert len(llm) == 15
    assert Counter(llm) == {
        "PlannerOutput": 1, "LearningObjective": 1, "RelevanceCheck": 1, "ContentOutput": 1,
        "ContentVerification": 1, "SlidesOutput": 1, "ExampleOutput": 1, "QuizQuestion": 4,
        "GroundednessAnswerabilityCheck": 4,
    }
    assert criteria == {
        "content_relevance_pass", "content_groundedness_pass",
        "quiz_1_groundedness_answerability_pass", "quiz_2_groundedness_answerability_pass",
        "quiz_3_groundedness_answerability_pass", "practice_1_groundedness_answerability_pass",
    }


def test_without_self_check_one_module_costs_10_calls(document, llm):
    """15 lượt trừ lượt kiểm Content và bốn lượt kiểm câu hỏi: 10. Chấm độ liên quan (Corrective
    RAG) không phải tự kiểm đầu ra nên vẫn chạy, và chỉ tiêu chí của nó còn trong evaluations."""
    criteria = _generate(document, self_check=False)

    assert len(llm) == 10
    assert "ContentVerification" not in llm and "GroundednessAnswerabilityCheck" not in llm
    assert criteria == {"content_relevance_pass"}


@pytest.mark.parametrize("self_check", [True, False])
def test_question_components_record_whether_they_were_self_checked(document, llm, self_check):
    db = SessionLocal()
    try:
        module = pipeline.run_module_generation(db, db.get(Document, document["doc"]), self_check=self_check)
        components = db.query(ModuleComponent).filter(
            ModuleComponent.module_id == module.id,
            ModuleComponent.type.in_([ComponentType.quiz, ComponentType.practice]),
        ).all()
    finally:
        db.close()

    assert len(components) == 2
    assert all(c.content["self_checked"] is self_check for c in components)


def test_evaluations_keep_only_content_and_quiz_criteria(document, llm):
    criteria = _generate(document, self_check=True)

    assert criteria and all(c.startswith(("content_", "quiz_", "practice_")) for c in criteria)


def test_without_self_check_a_quiz_question_still_fails_the_free_syntax_check(document, llm, monkeypatch):
    """Bẫy cú pháp không tốn lượt gọi mô hình nào, nên vẫn chặn câu quiz khi tắt tự kiểm."""
    absolute = OUTPUTS[QuizQuestion].model_copy(update={"choices": ["luôn luôn", "pa hai", "pa ba", "pa bốn"]})
    monkeypatch.setitem(OUTPUTS, QuizQuestion, absolute)

    db = SessionLocal()
    try:
        state = quiz_graph.generate_quiz_question(
            db, document["course"], "q", OUTPUTS[LearningObjective], "quiz", self_check=False,
        )
    finally:
        db.close()

    assert state["passed"] is False and "tuyệt đối" in state["fail_reason"]
    assert llm.count("QuizQuestion") == 2 and "GroundednessAnswerabilityCheck" not in llm
