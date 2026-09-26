"""Sinh một câu hỏi: truy hồi theo điều câu đó kiểm, sinh, kiểm, và trượt thì sinh lại đúng một lần.

Mô hình và truy hồi được thay bằng hàm giả, nên không tốn hạn mức nào."""

import subprocess
import sys
import tomllib
import uuid
from pathlib import Path

import pytest

from app.generation import common, content, quiz_graph
from app.generation.common import MAX_CORRECTION_RETRY
from app.generation.objective import LearningObjective
from app.generation.quiz_graph import (
    GroundednessAnswerabilityCheck,
    QuizQuestion,
    _syntax_iwf_check,
    generate_quiz_question,
)
from app.retrieval.search import ChunkScope

BACKEND = Path(__file__).resolve().parents[1]

LO = LearningObjective(
    bloom_level="understand", verb="giải thích", objective_text="Giải thích khái niệm",
    sub_objectives=["Nêu định nghĩa", "Phân biệt hai trường hợp", "Giải thích vai trò"],
)

BALANCED = QuizQuestion(
    common_misconceptions=["m1", "m2", "m3"], stem="Hỏi", reasoning="r",
    choices=["pa một", "pa hai", "pa ba", "pa bốn"], correct_index=1, explanation="lời giải",
)
ABSOLUTE = BALANCED.model_copy(update={"choices": ["luôn luôn", "pa hai", "pa ba", "pa bốn"]})

PASS = GroundednessAnswerabilityCheck(
    groundedness_pass=True, groundedness_reason="", answerability_pass=True, answerability_reason="",
    bloom_level_pass=True, bloom_level_reason="",
)
UNGROUNDED = PASS.model_copy(update={"groundedness_pass": False, "groundedness_reason": "ngoài nguồn"})


class FakeModel:
    """Trả lần lượt các câu hỏi và các kết quả kiểm cho trước (phần tử cuối lặp lại khi hết), và
    ghi lại mọi lượt gọi."""

    def __init__(self, questions=(BALANCED,), checks=(PASS,)):
        self.answers = {QuizQuestion: list(questions), GroundednessAnswerabilityCheck: list(checks)}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, prompt, response_model, system_instruction=None, **kwargs):
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

    monkeypatch.setattr(quiz_graph, "retrieve", fake_retrieve)
    return seen


def _use(monkeypatch, model):
    monkeypatch.setattr(quiz_graph, "complete_structured", model)
    return model


def test_a_question_that_passes_its_check_is_generated_once(monkeypatch, searches):
    model = _use(monkeypatch, FakeModel())

    result = generate_quiz_question(None, uuid.uuid4(), "Nêu định nghĩa", LO)

    assert model.names() == ["QuizQuestion", "GroundednessAnswerabilityCheck"]
    assert result["passed"] is True and result["fail_reason"] is None
    assert result["correction_retry_count"] == 0
    assert result["question"] == BALANCED


def test_a_question_that_fails_is_regenerated_exactly_once_with_the_reason(monkeypatch, searches):
    model = _use(monkeypatch, FakeModel(checks=[UNGROUNDED]))

    result = generate_quiz_question(None, uuid.uuid4(), "Nêu định nghĩa", LO)

    assert MAX_CORRECTION_RETRY == 1
    assert model.names() == ["QuizQuestion", "GroundednessAnswerabilityCheck"] * 2
    assert result["passed"] is False
    assert result["fail_reason"] == "Groundedness fail: ngoài nguồn"
    assert result["correction_retry_count"] == 1
    first, second = model.question_prompts()
    assert "ngoài nguồn" not in first and "Groundedness fail: ngoài nguồn" in second


def test_a_question_that_passes_on_the_second_try_is_kept(monkeypatch, searches):
    model = _use(monkeypatch, FakeModel(questions=[ABSOLUTE, BALANCED]))

    result = generate_quiz_question(None, uuid.uuid4(), "Nêu định nghĩa", LO)

    assert model.names().count("QuizQuestion") == 2
    assert result["passed"] is True and result["correction_retry_count"] == 1
    assert result["question"] == BALANCED
    assert "Syntax/IWF fail" in model.question_prompts()[1]


def test_each_question_retrieves_five_passages_for_what_it_asks_and_never_grades_them(monkeypatch, searches):
    model = _use(monkeypatch, FakeModel())
    scope = ChunkScope(uuid.uuid4(), 1, 2)

    generate_quiz_question(None, uuid.uuid4(), "Nêu định nghĩa", LO, scope=scope)

    assert searches == [{"query": "Nêu định nghĩa", "top_k": 5, "scope": scope}]
    assert "RelevanceCheck" not in model.names()


def test_without_self_check_the_model_never_checks_and_the_syntax_trap_still_blocks_quiz(monkeypatch, searches):
    model = _use(monkeypatch, FakeModel(questions=[ABSOLUTE]))

    result = generate_quiz_question(None, uuid.uuid4(), "q", LO, "quiz", self_check=False)

    assert model.names() == ["QuizQuestion", "QuizQuestion"]
    assert result["passed"] is False and "tuyệt đối" in result["fail_reason"]


def test_the_syntax_trap_does_not_block_practice(monkeypatch, searches):
    model = _use(monkeypatch, FakeModel(questions=[ABSOLUTE]))

    result = generate_quiz_question(None, uuid.uuid4(), "q", LO, "practice", self_check=False)

    assert model.names() == ["QuizQuestion"]
    assert result["passed"] is True and result["syntax_issues"]


def test_set_level_feedback_stays_on_the_retry_along_with_the_check_reason(monkeypatch, searches):
    model = _use(monkeypatch, FakeModel(checks=[UNGROUNDED]))

    generate_quiz_question(None, uuid.uuid4(), "q", LO, feedback="đáp án đúng dài nhất")

    first, second = model.question_prompts()
    assert "đáp án đúng dài nhất" in first
    assert "đáp án đúng dài nhất" in second and "ngoài nguồn" in second


def test_content_and_questions_share_one_self_check_loop():
    assert content._checked is common._checked
    assert quiz_graph._checked is common._checked


def test_nothing_imports_langgraph_any_more():
    script = (
        "import importlib, pkgutil, sys, app\n"
        "for info in pkgutil.walk_packages(app.__path__, 'app.'):\n"
        "    importlib.import_module(info.name)\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'langgraph'))\n"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert loaded == "[]"

    dependencies = tomllib.loads((BACKEND / "pyproject.toml").read_text())["project"]["dependencies"]
    assert not any(d.startswith("langgraph") for d in dependencies)


def _question(choices, explanation="ok"):
    return QuizQuestion(
        common_misconceptions=["a", "b", "c"],
        stem="stem",
        choices=choices,
        reasoning="why the first choice is right and the others are not",
        correct_index=0,
        explanation=explanation,
    )


def test_syntax_iwf_check_passes_balanced_clean_question():
    q = _question([
        "Đáp án đúng có độ dài tương đương",
        "Phương án nhiễu một cũng dài tương tự",
        "Phương án nhiễu hai cũng vậy luôn nhé",
        "Phương án nhiễu ba cũng cân bằng độ dài",
    ])
    assert _syntax_iwf_check(q) == []


def test_syntax_iwf_check_flags_absolute_words():
    q = _question([
        "Điều này luôn luôn đúng trong mọi trường hợp",
        "Phương án nhiễu một có độ dài tương tự đáp án",
        "Phương án nhiễu hai cũng có độ dài tương tự",
        "Phương án nhiễu ba cũng có độ dài tương tự vậy",
    ])
    issues = _syntax_iwf_check(q)
    assert any("tuyệt đối" in issue for issue in issues)


def test_syntax_iwf_check_flags_length_imbalance():
    q = _question([
        "Đúng",
        "Đây là một phương án nhiễu rất dài với nhiều chi tiết không cần thiết được thêm vào",
        "Ngắn",
        "Cũng ngắn",
    ])
    issues = _syntax_iwf_check(q)
    assert any("Độ dài" in issue for issue in issues)
