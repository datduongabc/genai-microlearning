"""The reasoning of a question is written before its answer is chosen, and the explanation is
drawn from it in the same model call: the module's Explanation component is those two fields of
each question, not a separate step.

Every model call is replaced by a fake."""

import types
import uuid

import pytest

from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.generation.example import Example, ExampleOutput
from app.generation.planner import Concept, PlannerOutput
from app.generation.quiz_graph import QuizQuestion
from app.generation.relevance import CorrectiveResult
from app.models import Chunk, ComponentType, Course, Document, DocumentStatus, User

REASONING = "REASONING-MARKER: B is right because the source says so; A, C and D each repeat a misconception."


def _question(**overrides) -> QuizQuestion:
    fields = {
        "common_misconceptions": ["m1", "m2", "m3"],
        "stem": "stem",
        "choices": ["A", "B", "C", "D"],
        "reasoning": REASONING,
        "correct_index": 1,
        "explanation": "short explanation",
    }
    return QuizQuestion(**{**fields, **overrides})


# --- the question schema -------------------------------------------------------------------------


def test_the_reasoning_comes_before_the_choices_and_the_answer_in_the_schema_the_model_fills_in():
    # The model fills the fields in the order they are declared: the misconceptions first, then the
    # reasoning about the right answer and the wrong ones, and only then the options and the key.
    schema = QuizQuestion.model_json_schema()
    fields = list(schema["properties"])

    assert fields.index("common_misconceptions") < fields.index("stem") < fields.index("reasoning")
    assert fields.index("reasoning") < fields.index("choices") < fields.index("correct_index")
    assert fields.index("correct_index") < fields.index("explanation")


def test_a_question_without_a_reasoning_is_not_valid():
    fields = {
        "common_misconceptions": ["a", "b", "c"], "stem": "s", "choices": ["A", "B", "C", "D"],
        "correct_index": 0, "explanation": "e",
    }

    with pytest.raises(ValueError):
        QuizQuestion(**fields)


# --- the pipeline --------------------------------------------------------------------------------


@pytest.fixture
def document():
    db = SessionLocal()
    try:
        user = User(email=f"expl-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Explanation grounding", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.pdf", status=DocumentStatus.embedded, raw_text="text")
        db.add(doc)
        db.flush()
        db.add_all(
            Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=f"LO-CHUNK-{i}") for i in range(3)
        )
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


def test_the_explanation_component_is_the_reasoning_and_explanation_the_question_was_written_with(
    document, monkeypatch
):
    objective = types.SimpleNamespace(objective_text="o", bloom_level="understand", sub_objectives=["first", "second"])

    monkeypatch.setattr(
        pipeline, "run_planner", lambda text, output_language="vi", custom_instructions="": PlannerOutput(concepts=[Concept(name="C", priority=1, summary="s")])
    )
    monkeypatch.setattr(
        pipeline, "retrieve",
        lambda db, course_id, query, top_k=8, candidate_k=30, min_k=2, scope=None: db.query(Chunk)
        .filter(Chunk.course_id == course_id).order_by(Chunk.chunk_index).limit(2).all(),
    )
    monkeypatch.setattr(
        pipeline, "corrective_retrieve", lambda objective, chunks, re_retrieve: CorrectiveResult(chunks, "high", 0)
    )
    monkeypatch.setattr(pipeline, "expand_with_parent_context", lambda db, chunks, window=2, scope=None: chunks)
    monkeypatch.setattr(
        pipeline, "generate_learning_objective",
        lambda concept, chunks, output_language="vi", custom_instructions="": objective,
    )
    monkeypatch.setattr(
        pipeline, "generate_content",
        lambda lo, chunks, self_check=True, output_language="vi", custom_instructions="": (
            types.SimpleNamespace(content="content", source_locators=[], claim_citations=[]),
            types.SimpleNamespace(groundedness_pass=True),
        ),
    )
    monkeypatch.setattr(
        pipeline, "generate_slides",
        lambda lo, content, output_language="vi", custom_instructions="": types.SimpleNamespace(slides=[]),
    )
    monkeypatch.setattr(
        pipeline, "generate_example",
        lambda lo, content, chunks, output_language="vi", custom_instructions="": ExampleOutput(examples=[Example(context="c", analysis="a")] * 2),
    )
    monkeypatch.setattr(
        pipeline, "generate_quiz_question",
        lambda db, course_id, query, lo, mode="quiz", scope=None, **kwargs: {
            "passed": True, "question": _question(), "chunks": [],
        },
    )

    db = SessionLocal()
    try:
        module = pipeline.run_module_generation(db, db.get(Document, document["doc"]))
        quiz = next(c for c in module.components if c.type == ComponentType.quiz)
        stored_reasoning = [q["reasoning"] for q in quiz.content["questions"]]
        explanation = next(c for c in module.components if c.type == ComponentType.explanation)
        items = explanation.content["items"]
        question_explanations = [q.explanation for q in quiz.questions]
    finally:
        db.close()

    assert {i["reasoning"] for i in items} == {REASONING}
    assert {i["explanation_text"] for i in items} == {"short explanation"}
    assert question_explanations and set(question_explanations) == {"short explanation"}
    # Kept with the quiz, so the teacher (and anyone auditing the answer) can read it.
    assert stored_reasoning and all(r == REASONING for r in stored_reasoning)
