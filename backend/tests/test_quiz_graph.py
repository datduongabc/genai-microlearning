from app.generation.quiz_graph import (
    MAX_CORRECTION_RETRY,
    QuizQuestion,
    _route_after_groundedness,
    _route_after_relevance,
    _syntax_iwf_check,
)
from app.generation.relevance import MAX_RELEVANCE_RETRY


def test_route_after_relevance_high_confidence_proceeds():
    assert _route_after_relevance({"relevance_confidence": "high"}) == "generate_question"


def test_route_after_relevance_low_confidence_retries_once():
    assert _route_after_relevance({"relevance_confidence": "low", "relevance_retry_count": 0}) == "retry_retrieve"


def test_route_after_relevance_low_confidence_proceeds_best_effort_after_retry_budget():
    # MAX_RELEVANCE_RETRY = 1 -> once the budget is exhausted, don't block
    # the whole pipeline forever; proceed with the best chunks available.
    state = {"relevance_confidence": "low", "relevance_retry_count": MAX_RELEVANCE_RETRY}
    assert _route_after_relevance(state) == "generate_question"


def test_route_after_groundedness_passed_ends():
    assert _route_after_groundedness({"passed": True}) == "done"


def test_route_after_groundedness_fails_retries_once():
    assert _route_after_groundedness({"passed": False, "correction_retry_count": 0}) == "retry_generate"


def test_route_after_groundedness_fails_permanently_after_retry_budget():
    state = {"passed": False, "correction_retry_count": MAX_CORRECTION_RETRY}
    assert _route_after_groundedness(state) == "failed"


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
