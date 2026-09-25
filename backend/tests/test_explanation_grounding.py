"""The reasoning of a question is written before its answer is chosen, and the explanation is
built from it and from the very passages the question was made from.

Every model call is replaced by a fake that records what it was asked for."""

import types
import uuid

import pytest

from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.generation import quiz_graph, relevance
from app.generation.example import Example, ExampleOutput
from app.generation.planner import Concept, PlannerOutput
from app.generation.quiz_graph import (
    ExplanationOutput,
    ExplanationVerification,
    GroundednessAnswerabilityCheck,
    QuizQuestion,
    generate_explanation,
    generate_quiz_question,
)
from app.generation.relevance import CorrectiveResult
from app.models import Chunk, ComponentType, Course, Document, DocumentStatus, User, UserRole

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


def _chunk(text: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(id=uuid.uuid4(), content=text, source_ref="{}")


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


# --- the explanation step ------------------------------------------------------------------------


@pytest.fixture
def prompts(monkeypatch):
    """Runs generate_explanation with a fake model that passes its own checks, and returns
    every (response model, prompt) it was asked."""
    asked: list[tuple[type, str]] = []

    def fake(prompt, response_model, system_instruction=None, **kwargs):
        asked.append((response_model, prompt))
        if response_model is ExplanationOutput:
            return ExplanationOutput(reasoning="r", explanation_text="t")
        return ExplanationVerification(
            reasoning_first_pass=True, reasoning_first_reason="",
            all_distractors_addressed_pass=True, all_distractors_addressed_reason="",
            grounded_pass=True, grounded_reason="",
        )

    monkeypatch.setattr(quiz_graph, "complete_structured", fake)
    return asked


def test_the_explanation_is_asked_to_build_on_the_reasoning_written_before_the_answer(prompts):
    generate_explanation(_question(), [_chunk("some passage")])

    generation = next(p for model, p in prompts if model is ExplanationOutput)
    assert "REASONING-MARKER" in generation


def test_the_explanation_check_sees_that_reasoning_too(prompts):
    generate_explanation(_question(), [_chunk("some passage")])

    verification = next(p for model, p in prompts if model is ExplanationVerification)
    assert "REASONING-MARKER" in verification


def test_the_explanation_and_its_check_read_only_the_passages_they_are_given(prompts):
    given = [_chunk("PASSAGE-ONE about the question"), _chunk("PASSAGE-TWO about the question")]

    generate_explanation(_question(), given)

    for _model, prompt in prompts:
        assert "PASSAGE-ONE" in prompt and "PASSAGE-TWO" in prompt


# --- the quiz graph hands its passages on --------------------------------------------------------


def test_the_finished_quiz_state_carries_the_passages_the_question_was_made_from(monkeypatch):
    used = [_chunk("QUESTION-PASSAGE-1"), _chunk("QUESTION-PASSAGE-2")]
    monkeypatch.setattr(quiz_graph, "retrieve", lambda *args, **kwargs: used)

    def fake(prompt, response_model, system_instruction=None, **kwargs):
        if response_model is relevance.RelevanceCheck:
            return relevance.RelevanceCheck(confidence="high", reason="")
        if response_model is QuizQuestion:
            return _question()
        return GroundednessAnswerabilityCheck(
            groundedness_pass=True, groundedness_reason="", answerability_pass=True, answerability_reason="",
            bloom_level_pass=True, bloom_level_reason="",
        )

    monkeypatch.setattr(quiz_graph, "complete_structured", fake)
    monkeypatch.setattr(relevance, "complete_structured", fake)
    lo = types.SimpleNamespace(objective_text="objective", bloom_level="understand", sub_objectives=["a", "b"])

    state = generate_quiz_question(None, uuid.uuid4(), "query", lo, mode="quiz")

    assert state["passed"] is True
    assert [c.content for c in state["chunks"]] == ["QUESTION-PASSAGE-1", "QUESTION-PASSAGE-2"]


# --- the pipeline --------------------------------------------------------------------------------


@pytest.fixture
def document():
    db = SessionLocal()
    try:
        user = User(email=f"expl-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", role=UserRole.teacher)
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


def test_the_explanation_is_made_from_the_passages_of_the_question_and_not_those_of_the_objective(
    document, monkeypatch
):
    question_chunks = [_chunk("QUESTION-PASSAGE-A"), _chunk("QUESTION-PASSAGE-B")]
    seen: dict = {}
    objective = types.SimpleNamespace(objective_text="o", bloom_level="understand", sub_objectives=["first", "second"])

    monkeypatch.setattr(
        pipeline, "run_planner", lambda text: PlannerOutput(concepts=[Concept(name="C", priority=1, summary="s")])
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
        lambda concept, chunks: (objective, types.SimpleNamespace(grounded_pass=True)),
    )
    monkeypatch.setattr(
        pipeline, "generate_content",
        lambda lo, chunks: (
            types.SimpleNamespace(content="content", source_locators=[], claim_citations=[]),
            types.SimpleNamespace(groundedness_pass=True),
        ),
    )
    monkeypatch.setattr(
        pipeline, "generate_slides",
        lambda lo, content: (types.SimpleNamespace(slides=[]), types.SimpleNamespace(no_new_info_pass=True)),
    )
    monkeypatch.setattr(
        pipeline, "generate_example",
        lambda lo, content, chunks: (
            ExampleOutput(examples=[Example(context="c", analysis="a")]), types.SimpleNamespace(no_overreach_pass=True)
        ),
    )
    monkeypatch.setattr(
        pipeline, "generate_quiz_question",
        lambda db, course_id, query, lo, mode="quiz", scope=None, **kwargs: {
            "passed": True, "question": _question(), "chunks": question_chunks,
        },
    )

    def fake_explanation(question, chunks):
        seen["chunks"] = chunks
        return (
            types.SimpleNamespace(reasoning="r", explanation_text="t"),
            types.SimpleNamespace(grounded_pass=True),
        )

    monkeypatch.setattr(pipeline, "generate_explanation", fake_explanation)

    db = SessionLocal()
    try:
        module = pipeline.run_module_generation(db, db.get(Document, document["doc"]))
        quiz = next(c for c in module.components if c.type == ComponentType.quiz)
        stored_reasoning = [q["reasoning"] for q in quiz.content["questions"]]
    finally:
        db.close()

    assert [c.content for c in seen["chunks"]] == ["QUESTION-PASSAGE-A", "QUESTION-PASSAGE-B"]
    # Kept with the quiz, so the teacher (and anyone auditing the answer) can read it.
    assert stored_reasoning and all(r == REASONING for r in stored_reasoning)
