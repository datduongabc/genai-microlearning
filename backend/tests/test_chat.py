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
    """Model có thể trả về một chunk_id trông hợp lý nhưng không nằm trong các đoạn đã truy hồi:
    trích dẫn đó trỏ tới chỗ không tồn tại, hoặc tới đoạn model chưa hề đọc."""
    import types
    import uuid

    from app.chat import service

    kept, other = uuid.uuid4(), uuid.uuid4()
    chunks = [types.SimpleNamespace(id=kept, source_ref="{}", content="Heap là cây nhị phân hoàn chỉnh.")]

    def fake(prompt, response_model, **kw):
        assert str(kept) in prompt
        return response_model(answer="Heap là cây nhị phân hoàn chỉnh.", citations=[
            {"chunk_id": str(kept)}, {"chunk_id": str(other)}, {"chunk_id": "chunk-bịa"},
        ])

    monkeypatch.setattr(service, "complete_structured", fake)

    result = answer_question("Heap là gì?", chunks, [])

    assert [c.chunk_id for c in result.citations] == [str(kept)]
