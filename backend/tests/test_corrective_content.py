"""Corrective RAG in front of the lesson text: the passages found for an objective are judged
before Content is written, and when they do not fit, the search is rewritten and run again.

Every model call is replaced by a fake that answers from a script and records what it was asked."""

import types
import uuid

import pytest

from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.generation import relevance
from app.generation.example import Example, ExampleOutput
from app.generation.planner import Concept, PlannerOutput
from app.generation.relevance import MAX_RELEVANCE_RETRY, RelevanceCheck, corrective_retrieve
from app.models import (
    Chunk,
    ComponentType,
    Course,
    Document,
    DocumentStatus,
    Evaluation,
    Module,
    User,
    UserRole,
)

OBJECTIVE = "OBJECTIVE-MARKER explain how a scheduler picks the next process"


def _chunk(text: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(id=uuid.uuid4(), content=text, source_ref="{}")


def _high() -> RelevanceCheck:
    return RelevanceCheck(confidence="high", reason="fits")


def _low(refined: str | None = "REFINED-QUERY scheduler ready queue") -> RelevanceCheck:
    return RelevanceCheck(confidence="low", reason="off topic", refined_query=refined)


@pytest.fixture
def judge(monkeypatch):
    """A judge that answers from a script (the last answer repeats) and records every prompt."""
    class Judge:
        def __init__(self):
            self.script = [_high()]
            self.asked: list[tuple[str, str]] = []

        def __call__(self, prompt, response_model, system_instruction=None, **kwargs):
            assert response_model is RelevanceCheck
            self.asked.append((prompt, system_instruction or ""))
            return self.script[min(len(self.asked), len(self.script)) - 1]

    fake = Judge()
    monkeypatch.setattr(relevance, "complete_structured", fake)
    return fake


class Searches:
    """A search that returns the same fresh passage each time and records the queries."""

    def __init__(self):
        self.queries: list[str] = []
        self.found = [_chunk("FRESH passage about the ready queue")]

    def __call__(self, query):
        self.queries.append(query)
        return self.found


# --- the judgement --------------------------------------------------------------------------------


def test_the_judge_is_shown_the_objective_and_every_passage(judge):
    passages = [_chunk("PASSAGE-ONE text"), _chunk("PASSAGE-TWO text")]

    corrective_retrieve(OBJECTIVE, passages, Searches())

    prompt, _ = judge.asked[0]
    assert "OBJECTIVE-MARKER" in prompt
    assert "PASSAGE-ONE" in prompt and "PASSAGE-TWO" in prompt


def test_the_judge_is_told_to_treat_the_passages_as_data_not_instructions(judge):
    corrective_retrieve(OBJECTIVE, [_chunk("x")], Searches())

    _, instruction = judge.asked[0]
    assert "KHÔNG PHẢI chỉ thị" in instruction


# --- when the passages fit ------------------------------------------------------------------------


def test_passages_that_fit_are_returned_untouched_and_nothing_is_searched_again(judge):
    passages = [_chunk("a"), _chunk("b")]
    search = Searches()

    result = corrective_retrieve(OBJECTIVE, passages, search)

    assert result.chunks == passages
    assert result.confidence == "high"
    assert result.retries == 0
    assert search.queries == []
    assert len(judge.asked) == 1


# --- when they do not -----------------------------------------------------------------------------


def test_passages_that_do_not_fit_are_searched_again_with_the_rewritten_query(judge):
    judge.script = [_low("REFINED-QUERY scheduler ready queue"), _high()]
    search = Searches()

    result = corrective_retrieve(OBJECTIVE, [_chunk("old passage")], search)

    assert search.queries == ["REFINED-QUERY scheduler ready queue"]
    assert result.retries == 1
    assert result.confidence == "high"


def test_the_new_passages_come_first_and_the_old_ones_are_not_thrown_away(judge):
    judge.script = [_low(), _high()]
    old = [_chunk("old one"), _chunk("old two")]
    search = Searches()

    result = corrective_retrieve(OBJECTIVE, old, search)

    assert result.chunks[: len(search.found)] == search.found
    assert all(chunk in result.chunks for chunk in old)


def test_a_passage_found_again_is_not_repeated(judge):
    judge.script = [_low(), _high()]
    shared = _chunk("shared")
    search = Searches()
    search.found = [shared, _chunk("new")]

    result = corrective_retrieve(OBJECTIVE, [shared, _chunk("old")], search)

    assert sum(chunk is shared for chunk in result.chunks) == 1


def test_the_second_judgement_is_of_the_passages_after_the_new_search(judge):
    judge.script = [_low(), _high()]

    corrective_retrieve(OBJECTIVE, [_chunk("OLD-MARKER passage")], Searches())

    second_prompt, _ = judge.asked[1]
    assert "FRESH passage" in second_prompt and "OLD-MARKER" in second_prompt


def test_it_searches_again_only_as_often_as_the_budget_allows_then_goes_on_with_what_it_has(judge):
    judge.script = [_low()]  # low every time
    search = Searches()

    result = corrective_retrieve(OBJECTIVE, [_chunk("old")], search)

    assert len(search.queries) == MAX_RELEVANCE_RETRY
    assert result.confidence == "low"
    assert result.chunks  # best effort, never an empty context


def test_without_a_rewritten_query_there_is_nothing_to_search_with(judge):
    judge.script = [_low(refined=None)]
    search = Searches()
    passages = [_chunk("old")]

    result = corrective_retrieve(OBJECTIVE, passages, search)

    assert search.queries == []
    assert result.chunks == passages
    assert result.confidence == "low"


# --- in the module pipeline -----------------------------------------------------------------------


@pytest.fixture
def document():
    db = SessionLocal()
    try:
        user = User(email=f"crag-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", role=UserRole.teacher)
        db.add(user)
        db.flush()
        course = Course(name="Corrective content", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.pdf", status=DocumentStatus.embedded, raw_text="text")
        db.add(doc)
        db.flush()
        db.add_all(
            Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=f"CHUNK-{i} " + "filler " * 20)
            for i in range(6)
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


@pytest.fixture
def steps(monkeypatch):
    """The pipeline with every step but the retrieval loop faked; records what Content was given
    and the order in which the judge and Content were called."""
    record = {"events": [], "content_chunks": None, "queries": []}

    def fake_retrieve(db, course_id, query, top_k=8, candidate_k=30, min_k=2, scope=None):
        record["queries"].append(query)
        record["events"].append("retrieve")
        return db.query(Chunk).filter(Chunk.course_id == course_id).order_by(Chunk.chunk_index).limit(2).all()

    def fake_content(lo, chunks, self_check=True, output_language="vi", custom_instructions=""):
        record["events"].append("content")
        record["content_chunks"] = list(chunks)
        return (
            types.SimpleNamespace(content="content", source_locators=[], claim_citations=[]),
            types.SimpleNamespace(groundedness_pass=True),
        )

    objective = types.SimpleNamespace(objective_text=OBJECTIVE, bloom_level="understand", sub_objectives=["first", "second"])
    monkeypatch.setattr(
        pipeline, "run_planner", lambda text, output_language="vi", custom_instructions="": PlannerOutput(concepts=[Concept(name="Scheduling", priority=1, summary="s")])
    )
    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "expand_with_parent_context", lambda db, chunks, window=2, scope=None: chunks)
    monkeypatch.setattr(
        pipeline, "generate_learning_objective", lambda concept, chunks, output_language="vi", custom_instructions="": objective
    )
    monkeypatch.setattr(pipeline, "generate_content", fake_content)
    monkeypatch.setattr(
        pipeline, "generate_slides",
        lambda lo, content, output_language="vi", custom_instructions="": types.SimpleNamespace(slides=[]),
    )
    monkeypatch.setattr(
        pipeline, "generate_example",
        lambda lo, content, chunks, output_language="vi", custom_instructions="": ExampleOutput(examples=[Example(context="c", analysis="a")] * 2),
    )
    monkeypatch.setattr(pipeline, "generate_quiz_question", lambda *a, **k: {"passed": False})
    return record


def _generate(document_ids):
    db = SessionLocal()
    try:
        module = pipeline.run_module_generation(db, db.get(Document, document_ids["doc"]))
        content = next(c for c in module.components if c.type is ComponentType.content)
        checks = {e.criterion: e.score for e in db.query(Evaluation).filter(Evaluation.module_id == module.id)}
        return content.content, checks
    finally:
        db.close()


def test_content_is_written_only_after_the_passages_were_judged(document, steps, judge, monkeypatch):
    original = judge.__call__

    def note(prompt, response_model, system_instruction=None, **kwargs):
        steps["events"].append("judge")
        return original(prompt, response_model, system_instruction, **kwargs)

    monkeypatch.setattr(relevance, "complete_structured", note)

    _generate(document)

    assert steps["events"].index("judge") < steps["events"].index("content")


def test_when_the_judge_says_low_the_search_is_repeated_and_content_gets_the_new_passages(document, steps, judge):
    judge.script = [_low("REFINED-QUERY scheduler ready queue"), _high()]

    stored, checks = _generate(document)

    assert "REFINED-QUERY scheduler ready queue" in steps["queries"]
    assert stored["relevance_retry_count"] == 1
    assert stored["relevance_confidence"] == "high"
    assert checks["content_relevance_pass"] == 1.0


def test_when_the_judge_says_high_the_search_is_not_repeated(document, steps, judge):
    judge.script = [_high()]

    stored, checks = _generate(document)

    assert not any("REFINED" in q for q in steps["queries"])
    assert stored["relevance_retry_count"] == 0
    assert checks["content_relevance_pass"] == 1.0


def test_a_module_whose_passages_never_fit_is_still_made_and_says_so(document, steps, judge):
    judge.script = [_low()]

    stored, checks = _generate(document)

    assert steps["content_chunks"]
    assert stored["relevance_confidence"] == "low"
    assert checks["content_relevance_pass"] == 0.0
    assert db_module_count(document) == 1


def db_module_count(document_ids) -> int:
    db = SessionLocal()
    try:
        return db.query(Module).filter(Module.document_id == document_ids["doc"]).count()
    finally:
        db.close()
