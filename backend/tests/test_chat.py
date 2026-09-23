from app.chat.service import answer_question, condense_query


def test_condense_query_no_history_returns_message_unchanged():
    assert condense_query("OOP là gì?", []) == "OOP là gì?"


def test_answer_question_no_context_chunks_returns_not_covered_without_llm_call():
    result = answer_question("bất kỳ câu hỏi nào", [], [])
    assert result.answer == "Tài liệu môn học không đề cập nội dung này."
    assert result.citations == []
