import asyncio
import re

import pytest

from app.chat.service import answer_question, condense_query_streamed


def _streams(monkeypatch, reply, asked=None):
    from app.chat import service

    async def model(prompt, system_instruction=None, **_):
        if asked is not None:
            asked.append(prompt)
        yield reply

    monkeypatch.setattr(service, "stream_text", model)


def test_a_question_without_history_is_searched_as_asked_without_a_model_call(monkeypatch):
    _streams(monkeypatch, "must not be asked", asked := [])
    assert asyncio.run(condense_query_streamed("OOP là gì?", [])) == "OOP là gì?"
    assert asked == []


def test_answer_question_no_context_chunks_returns_not_covered_without_llm_call():
    result = answer_question("bất kỳ câu hỏi nào", [], [])
    assert result.answer == "Tài liệu môn học không đề cập nội dung này."
    assert result.citations == []


# Model được thay bằng hàm giả ghi lại prompt: không tốn hạn mức, và chứng minh được điều code
# làm với câu trả lời của model chứ không phụ thuộc model trả lời gì.


def test_a_follow_up_question_is_rewritten_with_the_history_before_it_is_searched(monkeypatch):
    asked = []
    _streams(monkeypatch, "Heap có mấy tính chất?", asked)
    history = [{"role": "user", "content": "Heap là gì?"}, {"role": "assistant", "content": "Heap là cây nhị phân..."}]

    rewritten = asyncio.run(condense_query_streamed("Nó có mấy tính chất?", history))

    assert rewritten == "Heap có mấy tính chất?"
    assert "Heap là gì?" in asked[0] and "Nó có mấy tính chất?" in asked[0]


def test_an_empty_rewrite_falls_back_to_the_question_as_asked(monkeypatch):
    _streams(monkeypatch, "   ")

    assert asyncio.run(condense_query_streamed(
        "Nó có mấy tính chất?", [{"role": "user", "content": "Heap là gì?"}])) == "Nó có mấy tính chất?"


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


@pytest.mark.parametrize(("question", "evidence"), [
    ("Vun đống xuống là gì?", "ReheapDown pushes the root down to its correct location."),
    ("Vun đống xuống là gì?", "Vun đống xuống đẩy nút gốc xuống vị trí đúng."),
    ("What is reheap down?", "ReheapDown pushes the root down to its correct location."),
])
def test_chat_keeps_eighth_source_and_persists_its_citation(monkeypatch, question, evidence):
    """Exercise HTTP, real retrieval and persistence; model boundaries stay offline.

    A comparison can need a source below rank five even with a multilingual reranker.
    Live model quality is measured separately; this guards the context budget and language.
    """
    import uuid

    from fastapi.testclient import TestClient

    from app.chat import service
    from app.db import SessionLocal
    from app.ingestion.embeddings import embed_texts
    from app.main import app
    from app.models import Chunk, Course, Document, DocumentStatus, RefreshToken, User
    from app.retrieval import search
    from tests.auth_helpers import create_and_login
    from tests.sse import events

    client = TestClient(app)
    token = create_and_login(client, f"cross-language-{uuid.uuid4().hex}@example.com")["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    course_id = client.post("/courses", json={"name": "Cross-language"}, headers=headers).json()["id"]
    texts = [f"Course overview {i}: algorithms." for i in range(7)] + [evidence]
    with SessionLocal() as db:
        course = db.get(Course, uuid.UUID(course_id))
        owner_id = course.owner_id
        doc = Document(course_id=course.id, filename="notes.pdf", status=DocumentStatus.embedded)
        db.add(doc)
        db.flush()
        chunks = [Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=text,
                        source_ref='{"page": 12}', embedding=embedding)
                  for i, (text, embedding) in enumerate(zip(texts, embed_texts(texts), strict=True))]
        db.add_all(chunks)
        db.commit()
        evidence_id = str(chunks[-1].id)

    async def model(prompt, system_instruction=None, **_):
        assert f"CÂU HỎI: {question}" in prompt
        # The weakest passage sits in the middle of the prompt now (edges_first): cite it by its number there.
        cited = re.search(r"SOURCE CHUNK \[(S\d+)\]:\n[^\n]*\n" + re.escape(evidence), prompt)
        yield f"The root moves down [{cited.group(1)}]." if cited else "Tài liệu môn học không đề cập nội dung này"

    def scored(query, candidates, attempts=None, span=None):
        assert query == question
        assert len(candidates) == 8
        return sorted(((c, 0.1 if c.content == evidence else 0.2) for c in candidates),
                      key=lambda pair: pair[1], reverse=True)

    monkeypatch.setattr(service, "stream_text", model)
    monkeypatch.setattr(search, "rerank_scored", scored)
    try:
        response = client.post(f"/courses/{course_id}/chat/stream", json={"message": question}, headers=headers)
        assert response.status_code == 200
        result = dict(events(response))
        # The eighth passage is the first cited, so the reader sees it as source 1.
        assert result["done"]["content"] == "The root moves down [S1]."
        assert result["done"]["citations"] == [{"chunk_id": evidence_id, "source_ref": '{"page": 12}', "content": evidence}]
        history = client.get(f"/courses/{course_id}/chat/{result['session']['session_id']}/history",
                             headers=headers).json()
        assert history[0]["content"] == question
        assert history[1]["citations"] == result["done"]["citations"]
    finally:
        with SessionLocal() as db:
            db.query(Course).filter(Course.id == uuid.UUID(course_id)).delete(synchronize_session=False)
            db.query(RefreshToken).filter(RefreshToken.user_id == owner_id).delete(synchronize_session=False)
            db.query(User).filter(User.id == owner_id).delete(synchronize_session=False)
            db.commit()
        search.invalidate_sparse_cache(uuid.UUID(course_id))
