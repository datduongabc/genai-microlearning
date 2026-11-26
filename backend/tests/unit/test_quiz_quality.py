"""Quiz gate regressions: no provider or database calls."""

import json
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.generation import common, content
from app.generation import module_generation_pipeline as pipeline
from app.generation import quiz_graph as quiz
from app.generation.example import Example, ExampleOutput
from app.generation.objective import LearningObjective
from app.models import ComponentType, Job, JobStatus
from app.retrieval.search import ChunkScope

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


# --- generate_quiz_question retry/grounding/retrieval flow & schema invariants --------------------

BACKEND_ROOT = Path(__file__).resolve().parents[2]
GRAPH_LO = LearningObjective(
    bloom_level="understand",
    verb="giải thích",
    objective_text="Giải thích khái niệm",
    sub_objectives=["Nêu định nghĩa", "Phân biệt hai trường hợp", "Giải thích vai trò"],
)
BALANCED = quiz.QuizQuestion(
    common_misconceptions=["m1", "m2", "m3"],
    stem="Hỏi",
    reasoning="r",
    choices=["pa một", "pa hai", "pa ba", "pa bốn"],
    correct_index=1,
    explanation="lời giải",
)
ABSOLUTE = BALANCED.model_copy(update={"choices": ["luôn luôn", "pa hai", "pa ba", "pa bốn"]})
def _graph_review():
    return review(option_checks=[
        {"is_correct": i == 1, "distinct_pass": True, "plausible_pass": True,
         "reason": "Supported by S1" if i == 1 else "Plausible confusion, wrong for this stem"}
        for i in range(4)
    ])


GRAPH_PASS = answer(quiz.GroundednessAnswerabilityCheck, _graph_review())
UNGROUNDED = GRAPH_PASS.model_copy(update={"groundedness_pass": False, "groundedness_reason": "ngoài nguồn"})


class FakeQuizModel:
    def __init__(self, questions=(BALANCED,), checks=(GRAPH_PASS,)):
        self.answers = {quiz.QuizQuestion: list(questions), quiz.GroundednessAnswerabilityCheck: list(checks)}
        self.calls: list[tuple[str, str]] = []
        self.blind_reviews: list[str] = []

    def __call__(self, prompt, response_model, system_instruction=None, **kwargs):
        if response_model is quiz.OptionReview:
            self.blind_reviews.append(prompt)
            return answer(quiz.OptionReview, _graph_review())
        self.calls.append((response_model.__name__, prompt))
        queue = self.answers[response_model]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def names(self):
        return [name for name, _ in self.calls]

    def question_prompts(self):
        return [prompt for name, prompt in self.calls if name == "QuizQuestion"]


@pytest.fixture
def searches(monkeypatch):
    seen = []

    def fake_retrieve(db, course_id, query, top_k=8, scope=None, **kwargs):
        seen.append({"query": query, "top_k": top_k, "scope": scope, **kwargs})
        return []

    monkeypatch.setattr(quiz, "retrieve", fake_retrieve)
    return seen


def _use_quiz_model(monkeypatch, model):
    monkeypatch.setattr(quiz, "complete_structured", model)
    return model


def test_a_question_that_passes_its_check_is_generated_once(monkeypatch, searches):
    model = _use_quiz_model(monkeypatch, FakeQuizModel())

    result = quiz.generate_quiz_question(None, uuid.uuid4(), "Nêu định nghĩa", GRAPH_LO)

    assert model.names() == ["QuizQuestion", "GroundednessAnswerabilityCheck"]
    assert result["passed"] is True and result["fail_reason"] is None
    assert result["correction_retry_count"] == 0
    assert result["question"] == BALANCED


def test_a_question_that_fails_is_regenerated_exactly_once_with_the_reason(monkeypatch, searches):
    model = _use_quiz_model(monkeypatch, FakeQuizModel(checks=[UNGROUNDED]))

    result = quiz.generate_quiz_question(None, uuid.uuid4(), "Nêu định nghĩa", GRAPH_LO)

    assert common.MAX_CORRECTION_RETRY == 1
    assert model.names() == ["QuizQuestion", "GroundednessAnswerabilityCheck"] * 2
    assert result["passed"] is False
    assert result["fail_reason"] == "Groundedness fail: ngoài nguồn"
    assert result["correction_retry_count"] == 1
    first, second = model.question_prompts()
    assert "ngoài nguồn" not in first and "Groundedness fail: ngoài nguồn" in second


def test_a_draft_failing_the_syntax_trap_is_regenerated_before_the_model_checks_it(monkeypatch, searches):
    model = _use_quiz_model(monkeypatch, FakeQuizModel(questions=[ABSOLUTE, BALANCED]))

    result = quiz.generate_quiz_question(None, uuid.uuid4(), "Nêu định nghĩa", GRAPH_LO)

    assert model.names() == ["QuizQuestion", "QuizQuestion", "GroundednessAnswerabilityCheck"]
    assert result["passed"] is True and result["correction_retry_count"] == 1
    assert result["question"] == BALANCED
    assert "Syntax/IWF fail" in model.question_prompts()[1]


def test_the_syntax_retries_are_capped_for_the_whole_question(monkeypatch, searches):
    model = _use_quiz_model(monkeypatch, FakeQuizModel(questions=[ABSOLUTE]))

    result = quiz.generate_quiz_question(None, uuid.uuid4(), "Nêu định nghĩa", GRAPH_LO)

    assert quiz.SYNTAX_RETRY == 2
    assert model.names() == [
        "QuizQuestion", "QuizQuestion", "QuizQuestion",
        "GroundednessAnswerabilityCheck", "QuizQuestion", "GroundednessAnswerabilityCheck",
    ]
    assert result["passed"] is False and "tuyệt đối" in result["fail_reason"]


def test_a_retry_that_fails_more_criteria_than_the_first_attempt_is_dropped(monkeypatch, searches):
    worse = UNGROUNDED.model_copy(update={"bloom_level_pass": False, "bloom_level_reason": "sai cấp"})
    second = BALANCED.model_copy(update={"stem": "Hỏi lại"})
    model = _use_quiz_model(monkeypatch, FakeQuizModel(questions=[BALANCED, second], checks=[UNGROUNDED, worse]))

    result = quiz.generate_quiz_question(None, uuid.uuid4(), "Nêu định nghĩa", GRAPH_LO)

    assert model.names().count("QuizQuestion") == 2
    assert result["question"] == BALANCED
    assert result["fail_reason"] == "Groundedness fail: ngoài nguồn"
    assert result["correction_retry_count"] == 1


def test_among_drafts_failing_the_syntax_trap_the_one_with_fewest_issues_is_checked(monkeypatch, searches):
    two_issues = BALANCED.model_copy(update={"choices": ["luôn luôn", "tất cả đều đúng", "pa ba", "pa bốn"]})
    model = _use_quiz_model(monkeypatch, FakeQuizModel(questions=[ABSOLUTE, two_issues, two_issues, BALANCED]))

    result = quiz.generate_quiz_question(None, uuid.uuid4(), "Nêu định nghĩa", GRAPH_LO)

    checked = next(p for n, p in model.calls if n == "GroundednessAnswerabilityCheck")
    assert "luôn luôn" in checked and "tất cả đều" not in checked
    assert result["question"] == BALANCED


def test_each_question_retrieves_five_passages_for_what_it_asks_and_never_grades_them(monkeypatch, searches):
    model = _use_quiz_model(monkeypatch, FakeQuizModel())
    scope = ChunkScope(uuid.uuid4(), 1, 2)

    quiz.generate_quiz_question(None, uuid.uuid4(), "Nêu định nghĩa", GRAPH_LO, scope=scope)

    assert searches == [{"query": "Nêu định nghĩa", "top_k": 5, "candidate_k": 15, "scope": scope}]
    assert "RelevanceCheck" not in model.names()


@pytest.mark.parametrize("mode", ["quiz", "practice"])
def test_without_self_check_the_model_never_checks_and_the_syntax_trap_still_blocks(monkeypatch, searches, mode):
    model = _use_quiz_model(monkeypatch, FakeQuizModel(questions=[ABSOLUTE]))

    result = quiz.generate_quiz_question(None, uuid.uuid4(), "q", GRAPH_LO, mode, self_check=False)

    assert model.names() == ["QuizQuestion"] * (common.MAX_CORRECTION_RETRY + 1 + quiz.SYNTAX_RETRY)
    assert result["passed"] is False and "tuyệt đối" in result["fail_reason"]
    assert result["syntax_issues"]


def test_set_level_feedback_stays_on_the_retry_along_with_the_check_reason(monkeypatch, searches):
    model = _use_quiz_model(monkeypatch, FakeQuizModel(checks=[UNGROUNDED]))

    quiz.generate_quiz_question(None, uuid.uuid4(), "q", GRAPH_LO, feedback="đáp án đúng dài nhất")

    first, second = model.question_prompts()
    assert "đáp án đúng dài nhất" in first
    assert "đáp án đúng dài nhất" in second and "ngoài nguồn" in second


def test_content_and_questions_share_one_retry_budget_after_the_model_check():
    assert content._checked is common._checked
    assert quiz.MAX_CORRECTION_RETRY is common.MAX_CORRECTION_RETRY


def test_nothing_imports_langgraph_any_more():
    script = (
        "import importlib, pkgutil, sys, app\n"
        "for info in pkgutil.walk_packages(app.__path__, 'app.'):\n"
        "    importlib.import_module(info.name)\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'langgraph'))\n"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert loaded == "[]"

    dependencies = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    assert not any(d.startswith("langgraph") for d in dependencies)


def _rebalance_q(choices):
    return quiz.QuizQuestion(
        common_misconceptions=["m1", "m2", "m3"],
        stem="stem",
        reasoning="reasoning",
        choices=list(choices),
        correct_index=1,
        explanation="short",
    )


def test_rebalance_ties_single_question_and_worst_index_selection():
    long_c = ("ngắn", "một đáp án đúng dài hơn hẳn mọi phương án khác", "cũng ngắn", "ngắn nữa")
    not_long = ("dài nhất trong câu này", "B", "C", "D")
    mild = ("aaaa", "bbbbb", "cccc", "dddd")
    tie = ("dài bằng nhau", "dài bằng nhau", "C", "D")

    assert quiz.question_to_rebalance([_rebalance_q(long_c), _rebalance_q(not_long)]) is None
    assert quiz.question_to_rebalance([_rebalance_q(long_c)]) is None
    assert quiz.question_to_rebalance([_rebalance_q(tie), _rebalance_q(tie)]) is None
    # Tie must not count toward margins (only 1 strictly longest in 4 -> None).
    assert quiz.question_to_rebalance([
        _rebalance_q(tie), _rebalance_q(long_c), _rebalance_q(not_long), _rebalance_q(not_long)
    ]) is None
    # Exact 25% boundary (2 of 8) stays None; 2 of 2 and 2 of 3 trigger rebalance.
    assert quiz.question_to_rebalance([_rebalance_q(long_c)] * 2 + [_rebalance_q(not_long)] * 6) is None
    assert quiz.question_to_rebalance([_rebalance_q(long_c), _rebalance_q(long_c)]) == 0
    assert quiz.question_to_rebalance([_rebalance_q(long_c), _rebalance_q(long_c), _rebalance_q(not_long)]) == 0
    assert quiz.question_to_rebalance([
        _rebalance_q(mild), _rebalance_q(not_long), _rebalance_q(long_c), _rebalance_q(mild)
    ]) == 2
    assert quiz.question_to_rebalance([
        _rebalance_q(mild), _rebalance_q(long_c), _rebalance_q(mild)
    ]) == 1


def test_syntax_and_verify_boundary_guards():
    # Dissimilar >=5-word options must not trip near-duplicate detection.
    unrelated = question(choices=[
        "First distinct option with enough tokens",
        "Alternative separate idea presented right here",
        "Another third branch in this item",
        "Final fourth distractor of normal length",
    ])
    assert quiz._syntax_iwf_check(unrelated) == []

    # Empty option must not also emit length-imbalance error.
    empty_opt = question(choices=["", "mot phuong an binh thuong", "ngan hon", "vua du"])
    assert not any("Độ dài" in issue for issue in quiz._syntax_iwf_check(empty_opt))

    # Exact 2x boundary (20 vs 10) is permitted without flagging length imbalance.
    exact_2x = question(choices=["k" * 10, "a" * 20, "b" * 10, "c" * 10])
    assert not any("Độ dài" in issue for issue in quiz._syntax_iwf_check(exact_2x))

    # Key equal to longest distractor (60 vs 60) or slightly above (50 vs 49) does not trip key-dominance.
    tie_long = question(choices=["k" * 60, "a" * 60, "b" * 40, "c" * 40])
    slightly_long = question(choices=["k" * 50, "a" * 49, "b" * 49, "c" * 49])
    assert not any("dài vượt trội" in issue for issue in quiz._syntax_iwf_check(tie_long))
    assert not any("dài vượt trội" in issue for issue in quiz._syntax_iwf_check(slightly_long))

    # Exact key-dominance threshold (75 vs 60: ratio == 1.25 and diff == 15) must flag.
    exact_dom = question(choices=["k" * 75, "a" * 60, "b" * 60, "c" * 60])
    assert any("dài vượt trội" in issue for issue in quiz._syntax_iwf_check(exact_dom))

    # Invalid correct_index returns immediately even when self_check=True without touching choices[correct_index].
    assert not quiz._verify_question(question(correct_index=4), LO, "S1", "quiz", True).syntax_pass


def test_an_objective_and_examples_enforce_cardinalities():
    fields = {"bloom_level": "understand", "verb": "giải thích", "objective_text": "o"}
    assert len(LearningObjective(**fields, sub_objectives=["a", "b", "c", "d"]).sub_objectives) == 4
    for wrong in (["a"], ["a", "b", "c", "d", "e"]):
        with pytest.raises(ValidationError):
            LearningObjective(**fields, sub_objectives=wrong)

    example = Example(context="c", analysis="a")
    assert len(ExampleOutput(examples=[example] * 2).examples) == 2
    for count in (0, 1, 3):
        with pytest.raises(ValidationError):
            ExampleOutput(examples=[example] * count)
