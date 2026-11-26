"""Module editing uses one mocked model call and the real HTTP/database paths."""
import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

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
from app.retrieval.search import ChunkScope
from tests.auth_helpers import create_and_login

client = TestClient(app)


@pytest.fixture
def lesson():
    login = create_and_login(client, f"chat-edit-{uuid.uuid4().hex}@example.com")
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
        return response_model(target="slides", question_number=None, revised={"slides": [
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
        target="quiz", question_number=1, reply="Đã sửa câu 1.", revised={
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
        target=None, question_number=None, revised=None, reply="Bạn muốn sửa phần nào?"))
    response = send(lesson, "hello")
    assert response.status_code == 200
    assert response.json()["applied"] is False and response.json()["edit_count"] == 0
    with SessionLocal() as db:
        assert "ai_original" not in db.get(ModuleComponent, ids["slides"]).content
        assert len(db.get(ChatSession, uuid.UUID(response.json()["session_id"])).messages) == 2


def test_a_module_made_with_an_alias_the_gateway_dropped_is_edited_with_the_default(lesson, monkeypatch):
    from app.config import settings
    from app.llm import catalog
    from app.llm.gateway import requested_model

    ids, _ = lesson
    with SessionLocal() as db:
        job = db.query(Job).filter(Job.module_id == ids["module"]).one()
        job.payload = {**job.payload, "model_alias": "groq-pro"}
        db.commit()
    monkeypatch.setattr(catalog, "is_available", lambda alias: alias == settings.llm_model)
    used = []
    monkeypatch.setattr(service, "complete_structured", lambda prompt, response_model, **kw: used.append(
        requested_model()) or response_model(target=None, question_number=None, revised=None, reply="?"))
    assert send(lesson, "hello").status_code == 200
    assert used == [settings.llm_model]


def test_non_owner_gets_404(lesson):
    ids, _ = lesson
    other = create_and_login(client, f"other-chat-{uuid.uuid4().hex}@example.com")
    response = client.post(f"/modules/{ids['module']}/chat-edit", json={"message": "shorten slides"},
        headers={"Authorization": f"Bearer {other['access_token']}"})
    assert response.status_code == 404


@pytest.mark.parametrize("result", [
    {"target": "slides", "question_number": None, "revised": {"slides": []}},
    {"target": "quiz", "question_number": 99, "revised": {"slides": [{"title": "a", "bullets": ["a"]}, {"title": "b", "bullets": ["b"]}]}},
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
        return response_model(target=None, question_number=None, revised=None, reply="Bạn muốn sửa slide nào?")
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
    assert client.post(f"/courses/{ids['course']}/chat/stream", headers=headers,
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


def test_manual_component_edit_snapshots_original(lesson):
    ids, headers = lesson
    with SessionLocal() as db:
        original = copy.deepcopy(db.get(ModuleComponent, ids["slides"]).content)
    response = client.patch(f"/modules/{ids['module']}/components/{ids['slides']}", headers=headers,
        json={"content": {"slides": [{"title": "Manual", "bullets": ["x"]}]}})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert db.get(ModuleComponent, ids["slides"]).content == {
            "slides": [{"title": "Manual", "bullets": ["x"]}], "ai_original": original}


def test_large_scope_sends_only_retrieved_document_chunks(lesson, monkeypatch):
    ids, _ = lesson
    with SessionLocal() as db:
        document_id = db.get(Module, ids["module"]).document_id
        db.add_all([Chunk(document_id=document_id, course_id=ids["course"], chunk_index=i,
            content=f"Filler chunk {i}.", source_ref=f"lesson.txt#{i}") for i in range(1, 70)])
        db.commit()
    seen = {}
    def fake_retrieve(db, course_id, query, top_k, scope, wait):
        seen.update(course_id=course_id, query=query, scope=scope, wait=wait)
        return db.query(Chunk).filter(Chunk.document_id == document_id, Chunk.chunk_index == 0).all()
    monkeypatch.setattr(modules_api, "retrieve", fake_retrieve)
    prompts = []
    def fake(prompt, response_model, **kw):
        prompts.append(prompt)
        return response_model(target=None, question_number=None, revised=None, reply="Bạn muốn sửa slide nào?")
    monkeypatch.setattr(service, "complete_structured", fake)
    assert send(lesson, "shorten slide 1").status_code == 200
    assert seen["course_id"] == ids["course"] and seen["query"] == "shorten slide 1"
    assert seen["scope"] == ChunkScope(document_id)
    # Someone is waiting on this request, so a refused embedding must not wait out the limit.
    assert seen["wait"] is False
    assert "A stack is LIFO." in prompts[0] and "Filler chunk" not in prompts[0]


def test_large_scope_content_edit_keeps_citations_outside_retrieved_chunks(lesson, monkeypatch):
    ids, _ = lesson
    with SessionLocal() as db:
        document_id = db.get(Module, ids["module"]).document_id
        db.add_all([Chunk(document_id=document_id, course_id=ids["course"], chunk_index=i,
            content=f"Filler chunk {i}.", source_ref=f"lesson.txt#{i}") for i in range(1, 70)])
        # Saved before citations carried a chunk id: only the locator is stored.
        content = ModuleComponent(module_id=ids["module"], type=ComponentType.content, content={
            "markdown": "A stack is LIFO. Filler 42.", "source_locators": ["lesson.txt#42"],
            "claim_citations": [{"claim": "Filler 42.", "locator": "lesson.txt#42"}]})
        db.add(content)
        db.commit()
        content_id = content.id
        chunk_42 = str(db.query(Chunk).filter(Chunk.document_id == document_id, Chunk.chunk_index == 42).one().id)
    monkeypatch.setattr(modules_api, "retrieve", lambda db, course_id, query, top_k, scope, wait: db.query(Chunk).filter(
        Chunk.document_id == document_id, Chunk.chunk_index == 0).all())
    prompts = []

    def fake(prompt, response_model, **kw):
        prompts.append(prompt)
        # The retrieved chunk is S1; the one the content already cites follows it as S2.
        return response_model(
            target="content", question_number=None, revised={"content": "Filler 42. [S2]"},
            reply="Đã rút gọn nội dung.")

    monkeypatch.setattr(service, "complete_structured", fake)
    response = send(lesson, "shorten the content")
    assert response.status_code == 200, response.text
    assert '"claim": "Filler 42.", "source_id": "S2"' in prompts[0]
    with SessionLocal() as db:
        stored = db.get(ModuleComponent, content_id).content
        assert stored["markdown"] == "Filler 42. [S2]" and stored["source_locators"] == ["lesson.txt#42"]
        assert len(stored["inline_citations"]) == 1
        assert stored["inline_citations"][0]["claim"] == "Filler 42."
        assert stored["inline_citations"][0]["chunk_id"] == chunk_42
        assert "claim_citations" not in stored


def test_content_edit_citing_an_id_outside_the_prompt_is_rejected(lesson, monkeypatch):
    ids, _ = lesson
    with SessionLocal() as db:
        content = ModuleComponent(module_id=ids["module"], type=ComponentType.content, content={
            "markdown": "A stack is LIFO.", "source_locators": [], "claim_citations": []})
        db.add(content)
        db.commit()
        content_id, before = content.id, copy.deepcopy(content.content)
    monkeypatch.setattr(service, "complete_structured", lambda prompt, response_model, **kw: response_model(
        target="content", question_number=None, revised={"content": "Stack. [S7]"}, reply="Đã rút gọn nội dung."))
    assert send(lesson, "Rút gọn nội dung").status_code == 422
    with SessionLocal() as db:
        assert db.get(ModuleComponent, content_id).content == before


def test_content_edit_can_remove_a_sentence_and_its_citations(lesson, monkeypatch):
    ids, _ = lesson
    with SessionLocal() as db:
        content = ModuleComponent(module_id=ids["module"], type=ComponentType.content, content={
            "markdown": "A stack is LIFO.", "source_locators": ["lesson.txt"],
            "claim_citations": [{"claim": "A stack is LIFO.", "locator": "lesson.txt"}]})
        db.add(content)
        db.commit()
        content_id = content.id
    monkeypatch.setattr(service, "complete_structured", lambda prompt, response_model, **kw: response_model(
        target="content", question_number=None, revised={"content": "Stack."},
        reply="Đã rút gọn nội dung."))
    response = send(lesson, "Rút gọn nội dung")
    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.get(ModuleComponent, content_id).content["inline_citations"] == []


def test_content_edit_of_legacy_ambiguous_citations_can_leave_them_uncited(lesson, monkeypatch):
    ids, _ = lesson
    with SessionLocal() as db:
        document_id = db.get(Module, ids["module"]).document_id
        db.add_all([Chunk(document_id=document_id, course_id=ids["course"], chunk_index=i,
            content=f"Heading part {i}.", source_ref="lesson.html#Stack") for i in (1, 2)])
        # Every chunk under one heading shares the locator, so the saved citation resolves to none.
        content = ModuleComponent(module_id=ids["module"], type=ComponentType.content, content={
            "markdown": "A stack is LIFO.", "source_locators": ["lesson.html#Stack"],
            "claim_citations": [{"claim": "A stack is LIFO.", "locator": "lesson.html#Stack"}]})
        db.add(content)
        db.commit()
        content_id = content.id
    prompts = []

    def fake(prompt, response_model, **kw):
        prompts.append(prompt)
        return response_model(target="content", question_number=None, revised={
            "content": "Stack."}, reply="Đã rút gọn nội dung.")

    monkeypatch.setattr(service, "complete_structured", fake)
    response = send(lesson, "Rút gọn nội dung")
    assert response.status_code == 200, response.text
    assert '"existing_citations": []' in prompts[0]
    with SessionLocal() as db:
        assert db.get(ModuleComponent, content_id).content["markdown"] == "Stack."


def test_recorded_quiz_one_reply_edits_the_first_question(lesson, monkeypatch):
    # Adapted from a real Gemini output for "câu quiz 1" (key renamed to question_number, reply aligned).
    recorded = json.loads((Path(__file__).parent / "fixtures/quiz_chat_wrong_position.json").read_text())
    ids, _ = lesson
    with SessionLocal() as db:
        slides_before = copy.deepcopy(db.get(ModuleComponent, ids["slides"]).content)
    monkeypatch.setattr("app.llm.gateway._completion", lambda **kwargs: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(recorded)))]))
    response = send(lesson, "Làm câu quiz 1 khó hơn")
    assert response.status_code == 200, response.text
    assert response.json()["edit_count"] == 1
    with SessionLocal() as db:
        assert db.get(Question, ids["question"]).stem == recorded["revised"]["stem"]
        assert db.get(ModuleComponent, ids["quiz"]).content["ai_original"]["questions"][0]["stem"] == "Original question"
        assert db.get(ModuleComponent, ids["slides"]).content == slides_before


def test_quiz_number_two_edits_only_the_second_question(lesson, monkeypatch):
    ids, _ = lesson
    with SessionLocal() as db:
        db.add(Question(component_id=ids["quiz"], position=1, stem="Second question",
                        choices={"options": ["A", "B", "C", "D"]}, answer_key="0", explanation="Second"))
        db.commit()
    monkeypatch.setattr(service, "complete_structured", lambda prompt, response_model, **kw: response_model(
        target="quiz", question_number=2, reply="Đã sửa câu 2.", revised={
            "stem": "Harder second question", "choices": ["A", "B", "C", "D"],
            "correct_index": 2, "explanation": "A stack is LIFO.",
        }))
    assert send(lesson, "Làm câu quiz 2 khó hơn").status_code == 200
    with SessionLocal() as db:
        stems = {q.position: q.stem for q in db.get(ModuleComponent, ids["quiz"]).questions}
        assert stems == {0: "Original question", 1: "Harder second question"}


def test_manual_slide_edits_keep_original_and_study_packet_uses_it(lesson):
    from app.generation.study_packets import module_packet

    ids, headers = lesson
    with SessionLocal() as db:
        original = copy.deepcopy(db.get(ModuleComponent, ids["slides"]).content)
        module = db.get(Module, ids["module"])
        module.bloom_level = "apply"
        module.learning_objective = "Use a stack"
        practice = ModuleComponent(module_id=ids["module"], type=ComponentType.practice, content={})
        db.add(practice)
        db.flush()
        db.add(Question(component_id=practice.id, position=0, stem="Practice",
                        choices={"options": ["A", "B"]}, answer_key="0", explanation="AI"))
        db.commit()
    for title in ["Teacher first", "Teacher second"]:
        response = client.patch(f"/modules/{ids['module']}/components/{ids['slides']}",
                                headers=headers, json={"content": {
                                    "slides": [{"title": title, "bullets": ["Teacher bullet"]}],
                                    "ai_original": {"forged": True},
                                }})
        assert response.status_code == 200, response.text
        assert response.json()["content"]["ai_original"] == original
    with SessionLocal() as db:
        module = db.get(Module, ids["module"])
        packet = module_packet(module, ["Reference"])
        assert [s.model_dump() for s in packet.slides] == original["slides"]


def test_manual_content_edit_rederives_citations_and_cannot_replace_server_map(lesson):
    ids, headers = lesson
    with SessionLocal() as db:
        module = db.get(Module, ids['module'])
        chunk = db.query(Chunk).filter(Chunk.document_id == module.document_id).one()
        source_map = {'S1': {'chunk_id': str(chunk.id), 'locator': 'lesson.txt'}}
        component = ModuleComponent(module_id=module.id, type=ComponentType.content, content={
            'markdown': 'Old fact. [S1]', 'source_map': source_map,
            'inline_citations': [{'claim': 'Old fact.', 'source_id': 'S1', 'locator': 'lesson.txt',
                                  'chunk_id': str(chunk.id), 'start': 0, 'end': 9, 'marker_start': 10, 'marker_end': 14}]})
        db.add(component)
        db.commit()
        component_id = component.id
    url = f'/modules/{ids["module"]}/components/{component_id}'
    response = client.patch(url, headers=headers, json={'content': {
        'markdown': 'New fact. [S1]', 'source_map': {'S1': {'chunk_id': str(uuid.uuid4()), 'locator': 'forged'}}}})
    assert response.status_code == 200, response.text
    saved = response.json()['content']
    assert saved['source_map'] == source_map
    assert [c['claim'] for c in saved['inline_citations']] == ['New fact.']
    assert client.patch(url, headers=headers, json={'content': {'markdown': 'Bad. [S99]'}}).status_code == 422
    response = client.patch(url, headers=headers, json={'content': {'markdown': 'Uncited replacement.'}})
    assert response.status_code == 200
    assert response.json()['content']['inline_citations'] == []
    assert client.get(f'/modules/{ids["module"]}', headers=headers).json()['claim_citations'] == []


def test_manual_edit_of_map_less_content_keeps_bracketed_text_and_rejects_real_markers(lesson):
    ids, headers = lesson
    with SessionLocal() as db:
        component = ModuleComponent(module_id=ids['module'], type=ComponentType.content,
                                    content={'markdown': 'Store files in an [S3 bucket].'})
        db.add(component)
        db.commit()
        component_id = component.id
    url = f'/modules/{ids["module"]}/components/{component_id}'
    response = client.patch(url, headers=headers, json={'content': {
        'markdown': 'Store logs in an [S3 bucket] at [s3://logs].'}})
    assert response.status_code == 200, response.text
    assert response.json()['content']['markdown'] == 'Store logs in an [S3 bucket] at [s3://logs].'
    assert client.patch(url, headers=headers, json={'content': {'markdown': 'Fact. [S1]'}}).status_code == 422


def test_chat_remaps_old_codes_to_the_edit_prompt_and_rederives_saved_citations(lesson, monkeypatch):
    ids, _ = lesson
    with SessionLocal() as db:
        module = db.get(Module, ids['module'])
        second = Chunk(document_id=module.document_id, course_id=module.course_id, chunk_index=1,
                       content='Insertion takes logarithmic time.', source_ref='p2')
        db.add(second); db.flush()
        component = ModuleComponent(module_id=module.id, type=ComponentType.content, content={
            'markdown': 'Insertion takes logarithmic time. [S1]',
            'source_map': {'S1': {'chunk_id': str(second.id), 'locator': 'p2'}}, 'inline_citations': []})
        db.add(component); db.commit()
        component_id, second_id = component.id, str(second.id)
    def model(prompt, response_model, **kwargs):
        assert 'Insertion takes logarithmic time. [S2]' in prompt
        return response_model(target='content', question_number=None,
            revised={'content': 'Insertion has logarithmic cost. [S2]'}, reply='Đã sửa nội dung.')
    monkeypatch.setattr(service, 'complete_structured', model)
    response = send(lesson, 'rewrite the content')
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        saved = db.get(ModuleComponent, component_id).content
        assert saved['inline_citations'][0]['chunk_id'] == second_id
        assert saved['inline_citations'][0]['claim'] == 'Insertion has logarithmic cost.'
        assert saved['source_map']['S2']['chunk_id'] == second_id


def test_a_chat_edit_gives_up_before_cloudfront_does(lesson, monkeypatch):
    """CloudFront answers 504 at 60 s: a model call still waiting after that only holds one of the task's slots."""
    import litellm

    timeouts = []

    def fake(**kwargs):
        timeouts.append(kwargs["timeout"])
        return SimpleNamespace(model="x", usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
            {"target": None, "question_number": None, "revised": None, "reply": "Bạn muốn sửa slide nào?"})))])

    monkeypatch.setattr(litellm, "completion", fake)
    assert send(lesson).status_code == 200
    assert timeouts and all(t < 60 for t in timeouts)


def test_a_save_while_the_model_edits_a_question_is_not_written_over(lesson, monkeypatch):
    ids, _ = lesson

    def fake(prompt, response_model, **kw):
        # The lecturer saves the quiz while the model is still writing.
        with SessionLocal() as other:
            quiz = other.get(ModuleComponent, ids["quiz"])
            quiz.content = {**quiz.content, "saved_meanwhile": True}
            other.commit()
        return response_model(target="quiz", question_number=1, reply="Đã sửa câu 1.", revised={
            "common_misconceptions": ["m1", "m2", "m3"], "stem": "Which stack item leaves first?",
            "reasoning": "LIFO", "choices": ["First", "Last", "Middle", "Random"],
            "correct_index": 1, "explanation": "A stack is LIFO."})
    monkeypatch.setattr(service, "complete_structured", fake)

    assert send(lesson, "make quiz question 1 harder").status_code == 200
    with SessionLocal() as db:
        assert db.get(ModuleComponent, ids["quiz"]).content.get("saved_meanwhile") is True


def test_latest_module_chat_restores_history_isolated_and_resets_cleanly(lesson, monkeypatch):
    ids, headers = lesson
    latest_url = f"/modules/{ids['module']}/chat-edit/latest"
    sessions_url = f"/modules/{ids['module']}/chat-edit/sessions"

    assert client.get(latest_url, headers=headers).json() == {"session_id": None, "messages": []}

    monkeypatch.setattr(
        service, "complete_structured",
        lambda prompt, response_model, **kw: response_model(
            target=None, question_number=None, revised=None, reply="Đã ghi nhận yêu cầu."
        ),
    )
    first = send(lesson, "rút gọn slide 1").json()
    second = send(lesson, "thêm ví dụ thực tế", session_id=first["session_id"]).json()
    assert second["session_id"] == first["session_id"]

    restored = client.get(latest_url, headers=headers).json()
    assert restored["session_id"] == first["session_id"]
    assert [(m["role"], m["content"]) for m in restored["messages"]] == [
        ("user", "rút gọn slide 1"),
        ("assistant", "Đã ghi nhận yêu cầu."),
        ("user", "thêm ví dụ thực tế"),
        ("assistant", "Đã ghi nhận yêu cầu."),
    ]
    # Isolated from course chat and from sibling modules.
    assert client.get(f"/courses/{ids['course']}/chat/latest", headers=headers).json() == {"session_id": None, "messages": []}
    with SessionLocal() as db:
        module = db.get(Module, ids["module"])
        sibling = Module(course_id=module.course_id, document_id=module.document_id, title="Other module")
        db.add(sibling)
        db.commit()
        sibling_id = sibling.id

    assert client.get(f"/modules/{sibling_id}/chat-edit/latest", headers=headers).json() == {"session_id": None, "messages": []}

    fresh = client.post(sessions_url, headers=headers)
    again = client.post(sessions_url, headers=headers)
    assert fresh.status_code == 201 and again.status_code == 201
    assert fresh.json()["session_id"] == again.json()["session_id"] != first["session_id"]
    assert client.get(latest_url, headers=headers).json() == {"session_id": fresh.json()["session_id"], "messages": []}

    with SessionLocal() as db:
        job = db.query(Job).filter(Job.module_id == ids["module"]).one()
        job.payload = {"output_language": "en", "use_retrieval": False}
        db.commit()
    assert client.post(sessions_url, headers=headers).status_code == 422
