"""Quiz gate regressions: no provider or database calls."""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.generation import module_generation_pipeline as pipeline
from app.generation import quiz_graph as quiz
from app.generation.objective import LearningObjective
from app.models import ComponentType, Job, JobStatus

LO = LearningObjective(bloom_level="understand", verb="explain", objective_text="Explain OOP",
                       sub_objectives=["Define abstraction", "Compare inheritance and encapsulation"])


def question(**updates):
    return quiz.QuizQuestion(
        common_misconceptions=["m1", "m2", "m3"], stem="Which describes abstraction?",
        reasoning="Compare the options with S1.",
        choices=["Hide implementation details", "Derive a class from a parent",
                 "Bundle state with operations", "Dispatch by runtime object type"],
        correct_index=0, explanation="S1 describes abstraction.",
    ).model_copy(update=updates)


def review(**updates):
    return {
        "groundedness_pass": True, "groundedness_reason": "S1 supports the key",
        "answerability_pass": True, "answerability_reason": "One key",
        "bloom_level_pass": True, "bloom_level_reason": "Matches the target",
        "wording_pass": True, "wording_reason": "No clue",
        "option_checks": [
            {"is_correct": i == 0, "distinct_pass": True, "plausible_pass": True,
             "reason": "Supported by S1" if i == 0 else "Plausible confusion, wrong for this stem"}
            for i in range(4)
        ], **updates,
    }


@pytest.mark.parametrize("choices,fragment", [
    (["  CAFE\u0301! ", "café", "Runtime dispatch", "Class inheritance"], "trùng"),
    (["Hide implementation details behind an interface",
      "Hide implementation detail behind an interface", "Runtime dispatch", "Class inheritance"], "trùng"),
    (["A", "B", " ", "D"], "rỗng"),
])
def test_duplicate_near_duplicate_and_empty_options_fail(choices, fragment):
    assert any(fragment in issue for issue in quiz._syntax_iwf_check(question(choices=choices)))


@pytest.mark.parametrize("key", [-1, 4])
def test_invalid_key_is_reported_without_indexing_it(key):
    result = quiz._verify_question(question(correct_index=key), LO, "S1", "quiz", False)
    assert not result.syntax_pass


@pytest.mark.parametrize("first,second", [
    ("The lock permits read access to the file", "The lock does not permit read access to the file"),
    ("The task completes after exactly 10 clock cycles", "The task completes after exactly 11 clock cycles"),
    ("The loop continues while index <= maximum value", "The loop continues while index >= maximum value"),
])
def test_meaningful_option_differences_are_not_near_duplicates(first, second):
    q = question(choices=[first, second,
                          "Readers wait until the writer has finished",
                          "Writers share a single exclusive file handle"])
    assert quiz._syntax_iwf_check(q) == []


def test_moderately_long_key_giveaway_is_caught():
    q = question(choices=["Hide implementation details behind a public interface",
                          "Derive a class from its parent", "Bundle object state and methods",
                          "Dispatch by the runtime type"])
    assert quiz._syntax_iwf_check(q)


def test_strict_review_requires_all_four_option_verdicts():
    with pytest.raises(ValidationError):
        quiz.GroundednessAnswerabilityCheck(**review(option_checks=[]))
    with pytest.raises(ValidationError):
        quiz.GroundednessAnswerabilityCheck(**review(wording_pass="true"))


@pytest.mark.parametrize("defect", ["two_correct", "wrong_key", "paraphrase", "trivial", "wording", "bloom", "source"])
def test_semantic_failures_override_an_overall_pass(monkeypatch, defect):
    data = review()
    if defect == "two_correct":
        data["option_checks"][3]["is_correct"] = True
    elif defect == "wrong_key":
        data["option_checks"][0]["is_correct"] = False
        data["option_checks"][1]["is_correct"] = True
    elif defect in ("paraphrase", "trivial"):
        data["option_checks"][2]["distinct_pass" if defect == "paraphrase" else "plausible_pass"] = False
    else:
        field = {"wording": "wording", "bloom": "bloom_level", "source": "groundedness"}[defect]
        data[f"{field}_pass"] = False
        data[f"{field}_reason"] = defect
    monkeypatch.setattr(quiz, "complete_structured", lambda *a, **kw: quiz.GroundednessAnswerabilityCheck(**data))
    result = quiz._verify_question(question(), LO, "S1", "quiz", True)
    assert quiz.failed_criteria(result, quiz.QUESTION_CRITERIA)


@pytest.mark.parametrize("repair", [True, False])
def test_only_the_failed_question_is_regenerated_once(monkeypatch, repair):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "two_correct_abstraction.json").read_text())
    bad = question(**fixture)
    generated = []
    checked = []

    def model(prompt, schema, **kwargs):
        if schema is quiz.QuizQuestion:
            generated.append(prompt)
            return bad if len(generated) == 2 or (not repair and len(generated) == 3) else question(stem=f"Q{len(generated)}")
        checked.append(prompt)
        data = review()
        if len(checked) == 2 or (not repair and len(checked) == 3):
            data["option_checks"][0]["is_correct"] = False
            data["option_checks"][2]["is_correct"] = True
            data["option_checks"][3]["is_correct"] = True
            data["option_checks"][3]["reason"] = "D also describes abstraction"
        return schema(**data)

    monkeypatch.setattr(quiz, "complete_structured", model)
    monkeypatch.setattr(quiz, "retrieve", lambda *a, **kw: [])
    asked = pipeline._generate_questions(None, SimpleNamespace(course_id=uuid.uuid4()), LO, "OOP", None)
    assert len(generated) == len(checked) == 4  # three targets, only the middle one retried
    assert [a.state["correction_retry_count"] for a in asked] == [0, 1, 0]
    assert asked[1].state["passed"] is repair
    assert "D also describes abstraction" in generated[2]
    assert "Compare inheritance and encapsulation" in checked[1]
    if not repair:
        assert "D also describes abstraction" in asked[1].state["fail_reason"]


def test_exhausted_failure_does_not_complete_generation_job(monkeypatch):
    job = SimpleNamespace(id=uuid.uuid4(), document_id=uuid.uuid4(), payload={}, status=JobStatus.running)
    module = SimpleNamespace(id=uuid.uuid4(), components=[SimpleNamespace(
        type=ComponentType.quiz, content={"questions": [{"passed": False, "fail_reason": "C and D are correct"}]})])

    class DB:
        def get(self, model, pk):
            return job if model is Job else SimpleNamespace(raw_text="source")

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(pipeline, "WorkerSessionLocal", DB)
    monkeypatch.setattr(pipeline, "run_module_generation", lambda *a, **kw: module)
    pipeline.run_module_generation_step(job)
    assert job.status is JobStatus.failed
    assert job.module_id == module.id
    assert "C and D are correct" in job.last_error
