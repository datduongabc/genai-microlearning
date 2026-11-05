"""The chat stream over real HTTP, search and persistence, with the model boundaries offline."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.chat import service
from app.db import SessionLocal, request_engine
from app.ingestion.embeddings import embed_texts
from app.main import app
from app.models import ChatSession, Chunk, Course, Document, DocumentStatus, RefreshToken, User
from app.retrieval import search
from tests.auth_helpers import create_and_login


def _events(response) -> list[tuple[str, dict]]:
    events = []
    for block in response.text.split("\n\n"):
        if block.startswith("event:"):
            name, data = block.split("\n", 1)
            events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


@pytest.fixture
def world():
    client = TestClient(app)
    token = create_and_login(client, f"stream-{uuid.uuid4().hex}@example.com")["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    course_id = client.post("/courses", json={"name": "Stream"}, headers=headers).json()["id"]
    texts = ["A heap is a binary tree.", "A stack is last in first out."]
    with SessionLocal() as db:
        course = db.get(Course, uuid.UUID(course_id))
        owner_id = course.owner_id
        document = Document(course_id=course.id, filename="notes.pdf", status=DocumentStatus.embedded)
        db.add(document)
        db.flush()
        chunks = [Chunk(document_id=document.id, course_id=course.id, chunk_index=i, content=text, source_ref="p1",
                        embedding=vector) for i, (text, vector) in enumerate(zip(texts, embed_texts(texts), strict=True))]
        db.add_all(chunks)
        db.commit()
        chunk_ids = [str(c.id) for c in chunks]
    yield client, headers, course_id, chunk_ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == uuid.UUID(course_id)).delete(synchronize_session=False)
        db.query(RefreshToken).filter(RefreshToken.user_id == owner_id).delete(synchronize_session=False)
        db.query(User).filter(User.id == owner_id).delete(synchronize_session=False)
        db.commit()


def _scripted_model(monkeypatch, *pieces, prompts=None):
    async def model(prompt, system_instruction=None):
        if prompts is not None:
            prompts.append(prompt)
        for piece in pieces:
            yield piece

    monkeypatch.setattr(service, "stream_text", model)


@pytest.fixture
def passthrough_rerank(monkeypatch):
    monkeypatch.setattr(search, "rerank_scored", lambda query, chunks, attempts: [(c, 1.0) for c in chunks])


def test_the_answer_arrives_in_pieces_and_is_saved_with_its_citations(world, monkeypatch, passthrough_rerank):
    client, headers, course_id, chunk_ids = world
    _scripted_model(monkeypatch, "Heap là ", "cây nhị phân [", "S1].")

    response = client.post(f"/courses/{course_id}/chat/stream", json={"message": "Heap là gì?"}, headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response)
    assert [name for name, _ in events] == ["session", "delta", "delta", "delta", "done"]
    assert "[S1]" not in "".join(data["text"] for name, data in events if name == "delta")
    done = events[-1][1]
    assert done["content"] == "Heap là cây nhị phân."
    assert len(done["citations"]) == 1
    assert done["citations"][0]["chunk_id"] in chunk_ids
    session_id = events[0][1]["session_id"]
    history = client.get(f"/courses/{course_id}/chat/{session_id}/history", headers=headers).json()
    assert [m["content"] for m in history] == ["Heap là gì?", "Heap là cây nhị phân."]


def test_an_invented_source_is_dropped(world, monkeypatch, passthrough_rerank):
    client, headers, course_id, _ = world
    _scripted_model(monkeypatch, "Heap [S99].")

    done = _events(client.post(f"/courses/{course_id}/chat/stream", json={"message": "Heap?"}, headers=headers))[-1][1]

    assert done["citations"] == []


def test_a_follow_up_carries_the_earlier_turn(world, monkeypatch, passthrough_rerank):
    client, headers, course_id, _ = world
    _scripted_model(monkeypatch, "Heap là cây.")
    session_id = _events(client.post(f"/courses/{course_id}/chat/stream", json={"message": "Heap?"}, headers=headers))[0][1]["session_id"]
    prompts = []
    _scripted_model(monkeypatch, "Hai tính chất.", prompts=prompts)

    client.post(f"/courses/{course_id}/chat/stream", json={"message": "Nó có mấy tính chất?", "session_id": session_id},
                headers=headers)

    assert "Heap là cây." in prompts[0]
    assert "Nó có mấy tính chất?" not in prompts[0].split("CÂU HỎI MỚI NHẤT")[0]


def test_a_failed_reranker_costs_the_order_not_the_answer(world, monkeypatch):
    client, headers, course_id, _ = world
    _scripted_model(monkeypatch, "Trả lời.")

    def fail(query, chunks, attempts):
        assert attempts == search.CHAT_RERANK_ATTEMPTS
        raise search.httpx.ConnectError("reranker down")

    monkeypatch.setattr(search, "rerank_scored", fail)

    events = _events(client.post(f"/courses/{course_id}/chat/stream", json={"message": "Heap?"}, headers=headers))

    assert events[-1][0] == "done"


def test_a_model_that_fails_midway_ends_with_an_error_and_the_question_stays(world, monkeypatch, passthrough_rerank):
    client, headers, course_id, _ = world

    async def failing(prompt, system_instruction=None):
        yield "Heap "
        raise RuntimeError("secret provider detail")

    monkeypatch.setattr(service, "stream_text", failing)

    response = client.post(f"/courses/{course_id}/chat/stream", json={"message": "Heap?"}, headers=headers)

    events = _events(response)
    assert events[-1][0] == "error"
    assert "secret provider detail" not in response.text
    history = client.get(f"/courses/{course_id}/chat/{events[0][1]['session_id']}/history", headers=headers).json()
    assert [m["content"] for m in history] == ["Heap?"]


def test_no_connection_is_held_while_the_model_answers(world, monkeypatch, passthrough_rerank):
    client, headers, course_id, _ = world
    held = []

    async def model(prompt, system_instruction=None):
        held.append(request_engine.pool.checkedout())
        yield "Trả lời."

    monkeypatch.setattr(service, "stream_text", model)

    client.post(f"/courses/{course_id}/chat/stream", json={"message": "Heap?"}, headers=headers)

    assert held == [0]


def test_another_users_course_is_refused_before_anything_is_saved(world):
    client, _, course_id, _ = world
    stranger = create_and_login(client, f"stranger-{uuid.uuid4().hex}@example.com")["access_token"]

    response = client.post(f"/courses/{course_id}/chat/stream", json={"message": "Heap?"},
                           headers={"Authorization": f"Bearer {stranger}"})

    assert response.status_code in (403, 404)
    with SessionLocal() as db:
        assert db.query(ChatSession).filter(ChatSession.course_id == uuid.UUID(course_id)).count() == 0
