"""How a chat question is routed and traced, and what the reader's feedback does, over real HTTP and storage
with the model and the reranker offline."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.security import create_access_token
from app.chat import cache as chat_cache
from app.chat import service
from app.config import settings
from app.db import SessionLocal
from app.ingestion.embeddings import embed_texts
from app.main import app
from app.models import ChatMessage, Chunk, Course, CourseMember, Document, DocumentStatus, User
from app.retrieval import search
from tests.sse import events as _events

client = TestClient(app)


@pytest.fixture
def world(monkeypatch):
    chat_cache.clear_local()
    texts = ["A heap is a binary tree.", "A stack is last in first out."]
    with SessionLocal() as db:
        people = {name: User(email=f"route-{name}-{uuid.uuid4().hex[:8]}@example.com", name=name,
                             hashed_password="x") for name in ("owner", "learner")}
        db.add_all(people.values())
        db.flush()
        course = Course(name="Routes", owner_id=people["owner"].id)
        db.add(course)
        db.flush()
        db.add(CourseMember(course_id=course.id, user_id=people["learner"].id))
        document = Document(course_id=course.id, filename="notes.pdf", status=DocumentStatus.embedded,
                            learner_access=True)
        db.add(document)
        db.flush()
        db.add_all([Chunk(document_id=document.id, course_id=course.id, chunk_index=i, content=t, source_ref="p1",
                          embedding=v) for i, (t, v) in enumerate(zip(texts, embed_texts(texts), strict=True))])
        db.commit()
        ids = {**{k: u.id for k, u in people.items()}, "course": course.id, "document": document.id}
    calls = []

    async def model(prompt, system_instruction=None, **_):
        calls.append(prompt)
        yield "Heap là cây nhị phân [S1]."

    monkeypatch.setattr(service, "stream_text", model)
    monkeypatch.setattr(search, "rerank_scored", lambda query, chunks, attempts: [(c, 0.9) for c in chunks])
    yield ids, calls
    chat_cache.clear_local()
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids["owner"], ids["learner"]])).delete(synchronize_session=False)
        db.commit()


def _as(user_id):
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


def _ask(world, message, who="learner", session_id=None):
    ids, _ = world
    body = {"message": message, **({"session_id": session_id} if session_id else {})}
    events = _events(client.post(f"/courses/{ids['course']}/chat/stream", json=body, headers=_as(ids[who])))
    return events[0][1]["session_id"], events[-1][1]


def _trace(message_id):
    with SessionLocal() as db:
        return db.get(ChatMessage, uuid.UUID(message_id)).trace


def test_a_greeting_gets_a_fixed_reply_without_a_search_or_a_model(world, monkeypatch):
    monkeypatch.setattr(search, "search_for_chat", lambda *a, **k: pytest.fail("searched for a greeting"))
    _, done = _ask(world, "Xin chào!")
    assert done["content"].startswith("Chào bạn!")
    assert world[1] == []
    assert _trace(done["id"])["route"] == "small_talk"


def test_an_answer_is_traced_with_its_rewrite_passages_scores_and_timings(world):
    _, done = _ask(world, "Heap là gì?")
    trace = _trace(done["id"])
    assert trace["route"] == "answered"
    assert trace["top_score"] == 0.9
    assert [p["score"] for p in trace["passages"]] == [0.9, 0.9]
    assert {"embed_query", "bm25", "rrf", "total"} <= set(trace["seconds"])
    assert 0 < trace["first_token_seconds"] <= trace["seconds"]["total"]


def test_a_first_question_asked_again_is_answered_from_the_saved_answer(world):
    _ask(world, "Heap là gì?")
    _, again = _ask(world, "  heap LÀ gì ")
    assert again["content"] == "Heap là cây nhị phân [S1]."
    assert len(again["citations"]) == 1
    assert len(world[1]) == 1
    assert _trace(again["id"])["route"] == "cached"


def test_opening_a_document_to_learners_changes_which_saved_answers_they_get(world):
    ids, calls = world
    _ask(world, "Heap là gì?")
    with SessionLocal() as db:
        db.get(Document, ids["document"]).learner_access = False
        db.commit()
    _, closed = _ask(world, "Heap là gì?")
    # Nothing to search in: not covered, without a model call and not from the saved answer.
    assert closed["content"] == service.NOT_COVERED
    assert len(calls) == 1


def test_a_follow_up_is_never_answered_from_the_saved_answers(world):
    session_id, _ = _ask(world, "Heap là gì?")
    _ask(world, "Heap là gì?", session_id=session_id)
    assert len(world[1]) == 3  # the first answer, then a rewrite and an answer


def test_passages_below_the_threshold_are_not_covered_without_a_model_call(world, monkeypatch):
    monkeypatch.setattr(settings, "chat_min_rerank_score", 0.95)
    _, done = _ask(world, "Giá vàng hôm nay?")
    assert done["content"] == service.NOT_COVERED
    assert world[1] == []
    assert _trace(done["id"])["route"] == "off_topic"


def test_a_phone_number_in_a_question_is_not_stored_or_sent(world):
    ids, calls = world
    session_id, _ = _ask(world, "Heap là gì? Gọi mình 0912 345 678")
    assert "0912 345 678" not in calls[0]
    history = client.get(f"/courses/{ids['course']}/chat/{session_id}/history", headers=_as(ids["learner"])).json()
    assert "0912 345 678" not in history[0]["content"]


def test_feedback_reaches_the_lecturer_without_the_readers_name(world):
    ids, _ = world
    _, done = _ask(world, "Heap là gì?")
    url = f"/courses/{ids['course']}/chat/messages/{done['id']}"
    given = client.put(f"{url}/feedback", headers=_as(ids["learner"]),
                       json={"feedback": "wrong_citation", "citations": [1], "note": "Nguồn 1 nói về stack, mail a@b.vn"})
    assert given.status_code == 200
    assert (given.json()["feedback"], given.json()["feedback_citations"]) == ("wrong_citation", [1])
    assert client.post(f"{url}/opened", json={"citation": 1}, headers=_as(ids["learner"])).status_code == 204

    listed = client.get(f"/courses/{ids['course']}/chat/feedback", headers=_as(ids["owner"])).json()
    assert len(listed) == 1
    item = listed[0]
    assert (item["question"], item["feedback"], item["flagged_citations"]) == ("Heap là gì?", "wrong_citation", [1])
    assert "a@b.vn" not in item["note"]
    assert "content" not in item["citations"][0]
    assert str(ids["learner"]) not in str(item)
    with SessionLocal() as db:
        assert db.get(ChatMessage, uuid.UUID(done["id"])).opened_citations == [1]


def test_only_the_asker_gives_feedback_and_only_authors_read_it(world):
    ids, _ = world
    _, done = _ask(world, "Heap là gì?")
    url = f"/courses/{ids['course']}/chat/messages/{done['id']}/feedback"
    assert client.put(url, json={"feedback": "helpful"}, headers=_as(ids["owner"])).status_code == 404
    assert client.put(url, json={"feedback": "wrong_citation", "citations": [5]},
                      headers=_as(ids["learner"])).status_code == 422
    assert client.get(f"/courses/{ids['course']}/chat/feedback", headers=_as(ids["learner"])).status_code in (403, 404)
    # The function itself returns nothing to a caller who does not author the course.
    client.put(url, json={"feedback": "helpful"}, headers=_as(ids["learner"]))
    from app.db import RequestSessionLocal
    db = RequestSessionLocal()
    db.info["rls_user_id"] = ids["learner"]
    try:
        assert db.execute(text("SELECT count(*) FROM course_chat_feedback(:c)"), {"c": ids["course"]}).scalar() == 0
    finally:
        db.close()


def test_taking_feedback_back_clears_it(world):
    ids, _ = world
    _, done = _ask(world, "Heap là gì?")
    url = f"/courses/{ids['course']}/chat/messages/{done['id']}/feedback"
    client.put(url, json={"feedback": "not_right", "note": "sai"}, headers=_as(ids["learner"]))
    cleared = client.put(url, json={"feedback": None}, headers=_as(ids["learner"])).json()
    assert cleared["feedback"] is None
    assert client.get(f"/courses/{ids['course']}/chat/feedback", headers=_as(ids["owner"])).json() == []
