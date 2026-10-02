"""Module editing uses one mocked model call and the real HTTP/database paths."""
import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import modules as modules_api
from app.chat import service
from app.db import SessionLocal
from app.main import app
from app.models import (
    ChatMessage,
    ChatSession,
    Chunk,
    ComponentType,
    Course,
    Document,
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
    Question,
    RefreshToken,
    User,
)
from tests.auth_helpers import register_and_login

client = TestClient(app)


@pytest.fixture
def lesson():
    login = register_and_login(client, f"chat-edit-{uuid.uuid4().hex}@example.com")
    with SessionLocal() as db:
        course = Course(name="Chat edit", owner_id=uuid.UUID(login["user"]["id"]))
        db.add(course)
        db.flush()
        document = Document(course_id=course.id, filename="lesson.txt", raw_text="A stack is LIFO.")
        db.add(document)
        db.flush()
        module = Module(course_id=course.id, document_id=document.id, title="Stack")
        db.add(module)
        db.flush()
        slides = ModuleComponent(module_id=module.id, type=ComponentType.slides, content={"slides": [
            {"title": "Stack", "bullets": ["A stack is last in first out."]},
            {"title": "Order", "bullets": ["The last item leaves first."]},
        ]})
        quiz = ModuleComponent(module_id=module.id, type=ComponentType.quiz, content={"questions": []})
        db.add_all([slides, quiz, Chunk(document_id=document.id, course_id=course.id,
            chunk_index=0, content="A stack is LIFO.", source_ref="lesson.txt")])
        db.flush()
        question = Question(component_id=quiz.id, position=0, stem="Original question", choices={"options": ["A", "B", "C", "D"]}, answer_key="0", explanation="Original explanation")
        db.add(question)
        db.add(Job(module_id=module.id, document_id=document.id, job_type=JobType.module_generation,
            step="done", status=JobStatus.done, payload={"output_language": "en"}))
        db.commit()
        ids = {"module": module.id, "slides": slides.id, "quiz": quiz.id, "question": question.id, "course": course.id}
    yield ids, {"Authorization": f"Bearer {login['access_token']}"}
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(RefreshToken).filter(RefreshToken.user_id == uuid.UUID(login["user"]["id"])).delete(synchronize_session=False)
        db.query(User).filter(User.id == uuid.UUID(login["user"]["id"])).delete(synchronize_session=False)
        db.commit()


def send(lesson, message="shorten slide 1", **extra):
    ids, headers = lesson
    return client.post(f"/modules/{ids['module']}/chat-edit", json={"message": message, **extra}, headers=headers)


def test_slide_edit_is_scoped_preserves_original_and_counts_only_applied_turns(lesson, monkeypatch):
    ids, headers = lesson
    with SessionLocal() as db:
        original = copy.deepcopy(db.get(ModuleComponent, ids["slides"]).content)
    def fake(prompt, response_model, **kwargs):
        assert "A stack is LIFO." in prompt and str(ids["slides"]) in prompt
        assert "English" in kwargs["system_instruction"]
        return response_model(target="slides", question_position=None, revised={"slides": [
            {"title": "Stack", "bullets": ["LIFO"]}, original["slides"][1],
        ]}, reply="Đã rút gọn slide 1.")
    monkeypatch.setattr(service, "complete_structured", fake)
    response = send(lesson)
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True
    assert response.json()["edit_count"] == 1
    with SessionLocal() as db:
        slides = db.get(ModuleComponent, ids["slides"]).content
        assert slides["slides"][0]["bullets"] == ["LIFO"]
        assert slides["slides"][1] == original["slides"][1]
        assert slides["ai_original"] == original
        assert db.get(Question, ids["question"]).stem == "Original question"
        session = db.get(ChatSession, uuid.UUID(response.json()["session_id"]))
        assert session.module_id == ids["module"]
        assert len(session.messages) == 2
    assert client.get(f"/modules/{ids['module']}", headers=headers).json()["chat_edit_count"] == 1


def test_quiz_edit_snapshots_original_once(lesson, monkeypatch):
    ids, _ = lesson
    monkeypatch.setattr(service, "complete_structured", lambda prompt, response_model, **kw: response_model(
        target="quiz", question_position=0, reply="Đã sửa câu 1.", revised={
            "common_misconceptions": ["m1", "m2", "m3"], "stem": "Which stack item leaves first?",
            "reasoning": "LIFO", "choices": ["First", "Last", "Middle", "Random"],
            "correct_index": 1, "explanation": "A stack is LIFO.",
        }))
    assert send(lesson, "make quiz question 1 harder").status_code == 200
    with SessionLocal() as db:
        question = db.get(Question, ids["question"])
        assert question.stem == "Which stack item leaves first?" and question.answer_key == "1"
        original = copy.deepcopy(db.get(ModuleComponent, ids["quiz"]).content["ai_original"])
        assert original["questions"][0]["stem"] == "Original question"
    assert send(lesson, "make quiz question 1 harder").status_code == 200
    with SessionLocal() as db:
        assert db.get(ModuleComponent, ids["quiz"]).content["ai_original"] == original


def test_unclear_turn_changes_nothing_but_is_persisted(lesson, monkeypatch):
    ids, _ = lesson
    monkeypatch.setattr(service, "complete_structured", lambda prompt, response_model, **kw: response_model(
        target=None, question_position=None, revised=None, reply="Bạn muốn sửa phần nào?"))
    response = send(lesson, "hello")
    assert response.status_code == 200
    assert response.json()["applied"] is False and response.json()["edit_count"] == 0
    with SessionLocal() as db:
        assert "ai_original" not in db.get(ModuleComponent, ids["slides"]).content
        assert len(db.get(ChatSession, uuid.UUID(response.json()["session_id"])).messages) == 2


def test_non_owner_gets_404(lesson):
    ids, _ = lesson
    other = register_and_login(client, f"other-chat-{uuid.uuid4().hex}@example.com")
    response = client.post(f"/modules/{ids['module']}/chat-edit", json={"message": "shorten slides"},
        headers={"Authorization": f"Bearer {other['access_token']}"})
    assert response.status_code == 404


@pytest.mark.parametrize("result", [
    {"target": "slides", "question_position": None, "revised": {"slides": []}},
    {"target": "quiz", "question_position": 99, "revised": {"slides": [{"title": "a", "bullets": ["a"]}, {"title": "b", "bullets": ["b"]}]}},
])
def test_invalid_model_output_is_atomic(lesson, monkeypatch, result):
    ids, _ = lesson
    monkeypatch.setattr(service, "complete_structured", lambda prompt, response_model, **kw: response_model(**result, reply="Đã sửa"))
    response = send(lesson)
    assert response.status_code == 422, response.text
    with SessionLocal() as db:
        assert "ai_original" not in db.get(ModuleComponent, ids["slides"]).content
        assert db.get(Question, ids["question"]).stem == "Original question"
        assert db.query(ChatSession).filter(ChatSession.course_id == ids["course"]).count() == 0
        assert db.query(ChatMessage).join(ChatSession).filter(ChatSession.course_id == ids["course"]).count() == 0


def test_sessions_are_module_scoped_and_follow_up_keeps_turn_order(lesson, monkeypatch):
    ids, headers = lesson
    prompts = []
    def fake(prompt, response_model, **kw):
        prompts.append(prompt)
        return response_model(target=None, question_position=None, revised=None, reply="Bạn muốn sửa slide nào?")
    monkeypatch.setattr(service, "complete_structured", fake)
    first = send(lesson, "shorten it").json()
    assert send(lesson, "slide 1", session_id=first["session_id"]).status_code == 200
    assert prompts[1].index('"content": "shorten it"') < prompts[1].index('"content": "Bạn muốn sửa slide nào?"')
    with SessionLocal() as db:
        module = db.get(Module, ids["module"])
        other = Module(course_id=module.course_id, document_id=module.document_id, title="Another module")
        db.add(other)
        db.commit()
        other_id = other.id
    response = client.post(f"/modules/{other_id}/chat-edit", headers=headers,
        json={"message": "slide 1", "session_id": first["session_id"]})
    assert response.status_code == 404
    assert client.post(f"/courses/{ids['course']}/chat", headers=headers,
        json={"message": "what is a stack?", "session_id": first["session_id"]}).status_code == 404
    assert client.get(f"/courses/{ids['course']}/chat/{first['session_id']}/history", headers=headers).status_code == 404


def test_missing_source_asks_for_clarification_without_editing(lesson, monkeypatch):
    ids, _ = lesson
    with SessionLocal() as db:
        db.query(Chunk).filter(Chunk.course_id == ids["course"]).delete()
        db.commit()
    monkeypatch.setattr(service, "complete_structured", lambda *a, **kw: pytest.fail("No source must not invoke the model"))
    response = send(lesson)
    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert response.json()["edit_count"] == 0


def test_control_module_rejects_chat_edit(lesson, monkeypatch):
    ids, _ = lesson
    with SessionLocal() as db:
        job = db.query(Job).filter(Job.module_id == ids["module"]).one()
        job.payload = {"output_language": "en", "use_retrieval": False}
        db.commit()
    monkeypatch.setattr(service, "complete_structured", lambda *a, **kw: pytest.fail("Control module must not invoke the model"))
    response = send(lesson)
    assert response.status_code == 422
    with SessionLocal() as db:
        assert "ai_original" not in db.get(ModuleComponent, ids["slides"]).content
        assert db.query(ChatSession).filter(ChatSession.course_id == ids["course"]).count() == 0


def test_manual_component_edit_does_not_snapshot_original(lesson):
    ids, headers = lesson
    response = client.patch(f"/modules/{ids['module']}/components/{ids['slides']}", headers=headers,
        json={"content": {"slides": [{"title": "Manual", "bullets": ["x"]}]}})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert db.get(ModuleComponent, ids["slides"]).content == {"slides": [{"title": "Manual", "bullets": ["x"]}]}


def test_large_scope_sends_only_retrieved_document_chunks(lesson, monkeypatch):
    ids, _ = lesson
    with SessionLocal() as db:
        document_id = db.get(Module, ids["module"]).document_id
        db.add_all([Chunk(document_id=document_id, course_id=ids["course"], chunk_index=i,
            content=f"Filler chunk {i}.", source_ref=f"lesson.txt#{i}") for i in range(1, 70)])
        db.commit()
    seen = {}
    def fake_retrieve(db, course_id, query, top_k, scope):
        seen.update(course_id=course_id, query=query, scope=scope)
        return db.query(Chunk).filter(Chunk.document_id == document_id, Chunk.chunk_index == 0).all()
    monkeypatch.setattr(modules_api, "retrieve", fake_retrieve)
    prompts = []
    def fake(prompt, response_model, **kw):
        prompts.append(prompt)
        return response_model(target=None, question_position=None, revised=None, reply="Bạn muốn sửa slide nào?")
    monkeypatch.setattr(service, "complete_structured", fake)
    assert send(lesson, "shorten slide 1").status_code == 200
    assert seen["course_id"] == ids["course"] and seen["query"] == "shorten slide 1"
    assert (seen["scope"].document_id, seen["scope"].first_chunk_index, seen["scope"].last_chunk_index) == (document_id, 0, 69)
    assert "A stack is LIFO." in prompts[0] and "Filler chunk" not in prompts[0]


def test_large_scope_content_edit_keeps_citations_outside_retrieved_chunks(lesson, monkeypatch):
    ids, _ = lesson
    with SessionLocal() as db:
        document_id = db.get(Module, ids["module"]).document_id
        db.add_all([Chunk(document_id=document_id, course_id=ids["course"], chunk_index=i,
            content=f"Filler chunk {i}.", source_ref=f"lesson.txt#{i}") for i in range(1, 70)])
        content = ModuleComponent(module_id=ids["module"], type=ComponentType.content, content={
            "markdown": "A stack is LIFO. Filler 42.", "source_locators": ["lesson.txt#42"],
            "claim_citations": [{"claim": "Filler 42.", "locator": "lesson.txt#42"}]})
        db.add(content)
        db.commit()
        content_id = content.id
    monkeypatch.setattr(modules_api, "retrieve", lambda db, course_id, query, top_k, scope: db.query(Chunk).filter(
        Chunk.document_id == document_id, Chunk.chunk_index == 0).all())
    monkeypatch.setattr(service, "complete_structured", lambda prompt, response_model, **kw: response_model(
        target="content", question_position=None, revised={"content": "Filler 42.",
            "source_locators": ["lesson.txt#42"], "claim_citations": [{"claim": "Filler 42.", "locator": "lesson.txt#42"}]},
        reply="Đã rút gọn nội dung."))
    response = send(lesson, "shorten the content")
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        stored = db.get(ModuleComponent, content_id).content
        assert stored["markdown"] == "Filler 42." and stored["source_locators"] == ["lesson.txt#42"]


def test_recorded_quiz_reply_cannot_select_a_nonexistent_question(lesson, monkeypatch):
    # Real gemini-fast output after content/example turns selected position 1 for quiz 1.
    recorded = json.loads((Path(__file__).parent / "fixtures/quiz_chat_wrong_position.json").read_text())
    ids, _ = lesson
    with SessionLocal() as db:
        slides_before = copy.deepcopy(db.get(ModuleComponent, ids["slides"]).content)
    def completion(**kwargs):
        response_model = kwargs["response_format"]
        with pytest.raises(ValidationError):
            response_model.model_validate(recorded)
        corrected = {**recorded, "question_position": 0, "revised": {
            key: recorded["revised"][key] for key in ("stem", "choices", "correct_index", "explanation")
        }}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(corrected)))])
    monkeypatch.setattr("app.llm.gateway._completion", completion)
    response = send(lesson, "Làm câu quiz 1 khó hơn")
    assert response.status_code == 200, response.text
    assert response.json()["edit_count"] == 1
    with SessionLocal() as db:
        assert db.get(Question, ids["question"]).stem == recorded["revised"]["stem"]
        assert db.get(ModuleComponent, ids["quiz"]).content["ai_original"]["questions"][0]["stem"] == "Original question"
        assert db.get(ModuleComponent, ids["slides"]).content == slides_before
