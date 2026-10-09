from app.chat.service import answer_question, condense_query


def test_condense_query_no_history_returns_message_unchanged():
    assert condense_query("OOP là gì?", []) == "OOP là gì?"


def test_answer_question_no_context_chunks_returns_not_covered_without_llm_call():
    result = answer_question("bất kỳ câu hỏi nào", [], [])
    assert result.answer == "Tài liệu môn học không đề cập nội dung này."
    assert result.citations == []


# Model được thay bằng hàm giả ghi lại prompt: không tốn hạn mức, và chứng minh được điều code
# làm với câu trả lời của model chứ không phụ thuộc model trả lời gì.


def test_a_follow_up_question_is_rewritten_with_the_history_before_it_is_searched(monkeypatch):
    from app.chat import service

    asked = []
    monkeypatch.setattr(service, "complete", lambda prompt, **kw: asked.append(prompt) or "Heap có mấy tính chất?")
    history = [{"role": "user", "content": "Heap là gì?"}, {"role": "assistant", "content": "Heap là cây nhị phân..."}]

    rewritten = condense_query("Nó có mấy tính chất?", history)

    assert rewritten == "Heap có mấy tính chất?"
    assert "Heap là gì?" in asked[0] and "Nó có mấy tính chất?" in asked[0]


def test_an_empty_rewrite_falls_back_to_the_question_as_asked(monkeypatch):
    from app.chat import service

    monkeypatch.setattr(service, "complete", lambda prompt, **kw: "   ")

    assert condense_query("Nó có mấy tính chất?", [{"role": "user", "content": "Heap là gì?"}]) == "Nó có mấy tính chất?"


def test_citations_the_model_invents_are_dropped_and_only_retrieved_chunks_are_kept(monkeypatch):
    """The model cites the short ID a chunk carries in the prompt. An ID that is not in the prompt
    points at nothing, or at a passage the model never read, and is dropped, never guessed."""
    import types
    import uuid

    from app.chat import service

    first, second = uuid.uuid4(), uuid.uuid4()
    chunks = [
        types.SimpleNamespace(id=first, source_ref='{"page": 1}', content="Heap là cây nhị phân hoàn chỉnh."),
        types.SimpleNamespace(id=second, source_ref='{"page": 2}', content="Heap có hai loại."),
    ]

    def fake(prompt, response_model, **kw):
        assert "[S1]" in prompt and "[S2]" in prompt and str(first) not in prompt
        return response_model(answer="Heap là cây nhị phân hoàn chỉnh.", source_ids=["S2", "[S2]", "S9", "chunk-bịa"])

    monkeypatch.setattr(service, "complete_structured", fake)

    result = answer_question("Heap là gì?", chunks, [])

    assert [(c.chunk_id, c.source_ref) for c in result.citations] == [(str(second), '{"page": 2}')]
