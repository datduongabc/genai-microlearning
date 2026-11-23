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


def answer(schema, data):
    """The fields of one review that this call's schema asks for: the blind and the keyed half."""
    return schema(**{k: v for k, v in data.items() if k in schema.model_fields})


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


@pytest.mark.parametrize("extreme", [
    "Giai cấp vô sản phát triển hoàn toàn độc lập với phương thức sản xuất",
    "Mác không kế thừa bất kỳ tư tưởng nào của người đi trước",
    "Tác phẩm chỉ bàn về kinh tế thuần túy",
])
def test_extreme_wording_found_by_the_rubric_judges_is_caught(extreme):
    """Distractors of the 2026-11-08 modules that both judges said were eliminated without knowing the lesson."""
    q = question(choices=["Hide implementation details", extreme, "Bundle state with operations",
                          "Dispatch by runtime object type"])

    assert any("tuyệt đối" in issue for issue in quiz._syntax_iwf_check(q))


def test_strict_review_requires_all_four_option_verdicts():
    with pytest.raises(ValidationError):
        answer(quiz.OptionReview, review(option_checks=[]))
    with pytest.raises(ValidationError):
        answer(quiz.OptionReview, review(wording_pass="true"))


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
    monkeypatch.setattr(quiz, "complete_structured", lambda prompt, schema, **kw: answer(schema, data))
    result = quiz._verify_question(question(), LO, "S1", "quiz", True)
    assert quiz.failed_criteria(result, quiz.QUESTION_CRITERIA)


@pytest.mark.parametrize("repair", [True, False])
def test_only_the_failed_question_is_regenerated_once(monkeypatch, repair):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "two_correct_abstraction.json").read_text())
    bad = question(**fixture)
    generated = []
    checked = []
    reviews = []

    def model(prompt, schema, **kwargs):
        if schema is quiz.QuizQuestion:
            generated.append(prompt)
            return bad if len(generated) == 2 or (not repair and len(generated) == 3) else question(stem=f"Q{len(generated)}")
        data = review()
        if schema is quiz.OptionReview:
            reviews.append(prompt)
            if len(reviews) == 2 or (not repair and len(reviews) == 3):
                data["option_checks"][0]["is_correct"] = False
                data["option_checks"][2]["is_correct"] = True
                data["option_checks"][3]["is_correct"] = True
                data["option_checks"][3]["reason"] = "D also describes abstraction"
        else:
            checked.append(prompt)
        return answer(schema, data)

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


def test_exhausted_failure_still_completes_the_generation_job_with_its_module(monkeypatch):
    job = SimpleNamespace(id=uuid.uuid4(), document_id=uuid.uuid4(), payload={}, status=JobStatus.running, checkpoint=None)
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
    monkeypatch.setattr(pipeline, "hold", lambda session, held: held)
    monkeypatch.setattr(pipeline, "run_module_generation",
                        lambda db, *a, before_save, **kw: before_save(db, module) or module)
    pipeline.run_module_generation_step(job)
    assert job.status is JobStatus.done
    assert job.module_id == module.id


def test_the_blind_review_never_sees_the_key_reasoning_or_explanation(monkeypatch):
    """A reviewer shown the generator's answer and its reasoning tends to agree with it."""
    prompts = {}

    def model(prompt, schema, **kwargs):
        prompts[schema] = prompt
        return answer(schema, review())

    monkeypatch.setattr(quiz, "complete_structured", model)
    q = question(reasoning="SECRET-REASONING", explanation="SECRET-EXPLANATION")
    quiz._verify_question(q, LO, "S1", "quiz", True)

    blind = prompts[quiz.OptionReview]
    assert all(choice in blind for choice in q.choices)
    assert "SECRET" not in blind and "correct_index" not in blind
    assert "SECRET-EXPLANATION" in prompts[quiz.GroundednessAnswerabilityCheck]


@pytest.mark.parametrize("updates,fragment", [
    ({"stem": "Phát biểu nào sau đây KHÔNG đúng về trừu tượng hóa?"}, "phủ định"),
    ({"stem": "Which statement about abstraction is false?"}, "phủ định"),
    ({"stem": "Abstraction ___ the details of an implementation."}, "bỏ trống"),
    ({"explanation": "Phương án A đúng vì S1 nói vậy."}, "số thứ tự"),
    ({"choices": ["Hide implementation details", "All of the above", "Bundle state", "Dispatch by type"]}, "gộp"),
    ({"choices": ["Hide implementation details", "Sometimes derive a class", "Bundle state", "Dispatch by type"]}, "mơ hồ"),
])
def test_pattern_item_writing_flaws_are_caught(updates, fragment):
    assert any(fragment in issue for issue in quiz._syntax_iwf_check(question(**updates)))


@pytest.mark.parametrize("stem", [
    "Hàm chi phí đo lường mức độ sai sót tổng thể của mô hình bằng cách nào?",
    "Điểm khác biệt nào làm cho đồ thị của dãy số không phải là một đường liền nét?",
])
def test_ordinary_wording_is_not_taken_for_a_negative_stem(stem):
    """Two stored stems that a looser pattern flagged: "sai sót" and "không phải là" belong to the content."""
    assert quiz._syntax_iwf_check(question(stem=stem)) == []


def test_a_key_a_little_longer_than_the_longest_distractor_but_far_above_the_others_is_caught():
    """Lengths of a stored question: the key is 1.15 times the longest distractor, 1.35 times the median."""
    q = question(choices=["k" * 177, "a" * 154, "b" * 131, "c" * 134])
    assert any("dài vượt trội" in issue for issue in quiz._syntax_iwf_check(q))


def test_shuffling_keeps_the_key_and_is_stable():
    q = question(choices=["key", "d1", "d2", "d3"], correct_index=0)
    once, twice = quiz.shuffled_choices(q), quiz.shuffled_choices(q)
    assert once == twice and sorted(once.choices) == sorted(q.choices)
    assert once.choices[once.correct_index] == "key"


def test_the_key_lands_in_every_position_across_questions():
    positions = {quiz.shuffled_choices(question(stem=f"Q{i}", choices=["key", "d1", "d2", "d3"], correct_index=0))
                 .correct_index for i in range(40)}
    assert positions == {0, 1, 2, 3}


@pytest.mark.parametrize("longest,expected", [(1, False), (2, True)])
def test_two_long_keys_in_four_questions_trigger_a_rewrite(longest, expected):
    long_key = question(choices=["k" * 40, "a" * 30, "b" * 30, "c" * 30])
    short_key = question(choices=["k" * 30, "a" * 40, "b" * 30, "c" * 30])
    questions = [long_key] * longest + [short_key] * (4 - longest)
    assert (quiz.question_to_rebalance(questions) is not None) is expected
