"""Cấu trúc của một module: mỗi sub-objective một câu quiz (tối đa ba câu), một câu practice, các
ví dụ tối đa một cho mỗi phần, mỗi câu hỏi một lời giải thích là chính lời giải nó sinh kèm (không
tốn thêm lệnh gọi mô hình nào), và đáp án đúng không được là phương án dài nhất ở mọi câu của bài.

Mọi lệnh gọi mô hình được thay bằng hàm giả, nên không tốn hạn mức nào."""

import importlib
import pkgutil
import types
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import generation
from app.auth.security import create_access_token
from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.generation.example import Example, ExampleOutput
from app.generation.objective import LearningObjective
from app.generation.planner import Concept, PlannerOutput
from app.generation.quiz_graph import LENGTH_TELL_FEEDBACK, QuizQuestion, question_to_rebalance
from app.generation.relevance import CorrectiveResult
from app.main import app
from app.models import Chunk, ComponentType, Course, Document, DocumentStatus, User, UserRole

client = TestClient(app)


def _question(stem="stem", choices=("A", "B", "C", "D"), correct=1) -> QuizQuestion:
    return QuizQuestion(
        common_misconceptions=["m1", "m2", "m3"], stem=stem, reasoning="reasoning",
        choices=list(choices), correct_index=correct, explanation="short",
    )


LONG_CORRECT = ("ngắn", "một đáp án đúng dài hơn hẳn mọi phương án khác", "cũng ngắn", "ngắn nữa")


# --- đáp án đúng không được dài nhất ở mọi câu --------------------------------------------------


def test_no_question_is_redone_when_some_correct_answer_is_not_the_longest():
    questions = [_question(choices=LONG_CORRECT), _question(choices=("dài nhất trong câu này", "B", "C", "D"))]

    assert question_to_rebalance(questions) is None


def test_when_every_correct_answer_is_the_longest_the_most_obvious_one_is_redone():
    mild = ("aaaa", "bbbbb", "cccc", "dddd")
    questions = [_question(choices=mild), _question(choices=LONG_CORRECT), _question(choices=mild)]

    assert question_to_rebalance(questions) == 1


def test_a_single_question_whose_answer_is_longest_is_left_alone():
    assert question_to_rebalance([_question(choices=LONG_CORRECT)]) is None


def test_a_tie_for_longest_does_not_count_as_giving_the_answer_away():
    tie = ("dài bằng nhau", "dài bằng nhau", "C", "D")

    assert question_to_rebalance([_question(choices=tie), _question(choices=tie)]) is None


# --- learning objective ---------------------------------------------------------------------------


def test_an_objective_has_between_two_and_four_sub_objectives():
    fields = {"bloom_level": "understand", "verb": "giải thích", "objective_text": "o"}

    assert len(LearningObjective(**fields, sub_objectives=["a", "b", "c", "d"]).sub_objectives) == 4
    for wrong in (["a"], ["a", "b", "c", "d", "e"]):
        with pytest.raises(ValidationError):
            LearningObjective(**fields, sub_objectives=wrong)


# --- pipeline -------------------------------------------------------------------------------------


@pytest.fixture
def document():
    db = SessionLocal()
    try:
        user = User(email=f"shape-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x",
                    role=UserRole.teacher)
        db.add(user)
        db.flush()
        course = Course(name="Module structure", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.pdf", status=DocumentStatus.embedded, raw_text="text")
        db.add(doc)
        db.flush()
        db.add_all(Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=f"C{i}") for i in range(3))
        db.commit()
        ids = {"user": user.id, "course": course.id, "doc": doc.id}
    finally:
        db.close()
    yield ids
    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _chunk(text):
    return types.SimpleNamespace(id=uuid.uuid4(), content=text, source_ref="{}")


@pytest.fixture
def run(document, monkeypatch):
    """Chạy pipeline với các bước sinh giả. `questions` quyết định từng lượt sinh câu hỏi trả gì
    (theo thứ tự gọi); mọi lượt được ghi lại để kiểm. Mọi bước sinh đều được thay bằng hàm giả,
    nên lệnh gọi mô hình nào còn lọt tới `complete_structured` đều bị đếm vào `calls["llm"]`."""
    calls = {"quiz": [], "llm": []}

    def counting_llm(prompt, response_model, system_instruction=None, **kwargs):
        calls["llm"].append(response_model.__name__)
        raise AssertionError(f"unexpected model call for {response_model.__name__}")

    for info in pkgutil.iter_modules(generation.__path__):
        module = importlib.import_module(f"app.generation.{info.name}")
        if hasattr(module, "complete_structured"):
            monkeypatch.setattr(module, "complete_structured", counting_llm)

    def install(sub_objectives, questions):
        answers = iter(questions)
        lo = types.SimpleNamespace(objective_text="o", bloom_level="understand", sub_objectives=sub_objectives)
        monkeypatch.setattr(pipeline, "run_planner",
                            lambda text: PlannerOutput(concepts=[Concept(name="C", priority=1, summary="s")]))
        monkeypatch.setattr(pipeline, "retrieve", lambda db, course_id, query, **kw: db.query(Chunk).filter(
            Chunk.course_id == course_id).order_by(Chunk.chunk_index).limit(2).all())
        monkeypatch.setattr(pipeline, "corrective_retrieve",
                            lambda objective, chunks, re_retrieve: CorrectiveResult(chunks, "high", 0))
        monkeypatch.setattr(pipeline, "expand_with_parent_context", lambda db, chunks, **kw: chunks)
        monkeypatch.setattr(pipeline, "generate_learning_objective",
                            lambda concept, chunks: lo)
        monkeypatch.setattr(pipeline, "generate_content", lambda lo, chunks, self_check=True: (
            types.SimpleNamespace(content="content", source_locators=[], claim_citations=[]),
            types.SimpleNamespace(groundedness_pass=True)))
        monkeypatch.setattr(pipeline, "generate_slides", lambda lo, content: types.SimpleNamespace(slides=[]))
        monkeypatch.setattr(pipeline, "generate_example", lambda lo, content, chunks: ExampleOutput(
            examples=[Example(context="c1", analysis="a1"), Example(context="c2", analysis="a2")]))

        def fake_quiz(db, course_id, query, lo, mode="quiz", scope=None, feedback=None, self_check=True, lesson=None):
            question = next(answers)
            calls["quiz"].append({"query": query, "mode": mode, "feedback": feedback})
            passages = [_chunk(f"PASSAGE-OF-{query}")]
            return {"passed": question is not None, "question": question, "chunks": passages}

        monkeypatch.setattr(pipeline, "generate_quiz_question", fake_quiz)

        db = SessionLocal()
        try:
            module = pipeline.run_module_generation(db, db.get(Document, document["doc"]))
            parts = {
                c.type: (c.content, [(q.position, q.stem, q.explanation) for q in c.questions])
                for c in module.components
            }
            return module.id, parts
        finally:
            db.close()

    return install, calls, document


def test_one_quiz_question_per_sub_objective_in_order_then_one_practice_question(run):
    install, calls, _ = run
    _, parts = install(["s1", "s2", "s3", "s4"], [_question(stem=f"Q{i}") for i in range(4)])

    quiz_calls = [c for c in calls["quiz"] if c["mode"] == "quiz"]
    assert [c["query"] for c in quiz_calls] == ["s1", "s2", "s3"]
    assert [p for p, _, _ in parts[ComponentType.quiz][1]] == [0, 1, 2]
    assert [s for _, s, _ in parts[ComponentType.quiz][1]] == ["Q0", "Q1", "Q2"]
    assert [s for _, s, _ in parts[ComponentType.practice][1]] == ["Q3"]


def test_every_question_keeps_the_explanation_it_was_generated_with_at_no_extra_model_call(run):
    install, calls, _ = run
    questions = [
        _question(stem=f"Q{i}").model_copy(update={"reasoning": f"R-Q{i}", "explanation": f"E-Q{i}"})
        for i in range(3)
    ]
    _, parts = install(["s1", "s2"], questions)

    assert calls["llm"] == []
    assert [e for _, _, e in parts[ComponentType.quiz][1]] == ["E-Q0", "E-Q1"]
    assert [e for _, _, e in parts[ComponentType.practice][1]] == ["E-Q2"]
    items = parts[ComponentType.explanation][0]["items"]
    assert items == [
        {"component": "quiz", "position": 0, "reasoning": "R-Q0", "explanation_text": "E-Q0"},
        {"component": "quiz", "position": 1, "reasoning": "R-Q1", "explanation_text": "E-Q1"},
        {"component": "practice", "position": 0, "reasoning": "R-Q2", "explanation_text": "E-Q2"},
    ]


def test_every_example_is_kept(run):
    install, _, _ = run
    _, parts = install(["s1", "s2"], [_question(stem=f"Q{i}") for i in range(3)])

    assert parts[ComponentType.example][0]["examples"] == [
        {"context": "c1", "analysis": "a1"}, {"context": "c2", "analysis": "a2"},
    ]


def test_when_every_answer_is_the_longest_one_question_is_redone_with_the_reason_and_replaced(run):
    install, calls, _ = run
    telling = [_question(stem=f"Q{i}", choices=LONG_CORRECT) for i in range(3)]
    balanced = _question(stem="REDONE", choices=("một phương án khá dài", "B", "C", "D"))

    _, parts = install(["s1", "s2"], [*telling, balanced])

    redo = calls["quiz"][-1]
    assert redo["feedback"] == LENGTH_TELL_FEEDBACK
    all_stems = [s for t in (ComponentType.quiz, ComponentType.practice) for _, s, _ in parts[t][1]]
    assert "REDONE" in all_stems and len(all_stems) == 3


def test_a_redo_that_produces_no_question_keeps_the_original_one(run):
    install, _, _ = run
    telling = [_question(stem=f"Q{i}", choices=LONG_CORRECT) for i in range(3)]

    _, parts = install(["s1", "s2"], [*telling, None])

    all_stems = [s for t in (ComponentType.quiz, ComponentType.practice) for _, s, _ in parts[t][1]]
    assert sorted(all_stems) == ["Q0", "Q1", "Q2"]


def test_the_module_api_returns_questions_in_their_order(run):
    install, _, document = run
    module_id, _ = install(["s1", "s2", "s3"], [_question(stem=f"Q{i}") for i in range(4)])

    response = client.get(
        f"/modules/{module_id}", headers={"Authorization": f"Bearer {create_access_token(document['user'])}"}
    )

    quiz = next(c for c in response.json()["components"] if c["type"] == "quiz")
    assert [(q["position"], q["stem"]) for q in quiz["questions"]] == [(0, "Q0"), (1, "Q1"), (2, "Q2")]
