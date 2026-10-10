"""Generating again never overwrites a module, and the owner can delete one together with the
no-RAG control module paired with it."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.rate_limit import limiter
from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import (
    ChatMessage,
    ChatRole,
    ChatSession,
    ComponentType,
    Course,
    Document,
    DocumentStatus,
    Evaluation,
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
    Question,
    User,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)


def _headers(user_id) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _add_module(db, course, doc, title, *, control_of=None) -> Module:
    """A finished module with one of everything that hangs off it, and the job that made it."""
    module = Module(document_id=doc.id, course_id=course.id, title=title, learning_objective="LO", bloom_level="understand",
                    sub_objectives=["a", "b"])
    db.add(module)
    db.flush()
    quiz = ModuleComponent(module_id=module.id, type=ComponentType.quiz, content={})
    db.add_all([ModuleComponent(module_id=module.id, type=ComponentType.content, content={"markdown": "x"}), quiz])
    db.flush()
    db.add(Question(component_id=quiz.id, stem="q", choices={"options": ["A", "B"]}, answer_key="0"))
    db.add(Evaluation(module_id=module.id, criterion="accuracy", score=1.0))
    session = ChatSession(course_id=course.id, module_id=module.id, title="edit")
    db.add(session)
    db.flush()
    db.add(ChatMessage(session_id=session.id, role=ChatRole.user, content="shorten", citations=[]))
    payload = {} if control_of is None else {"use_retrieval": False, "paired_module_id": str(control_of.id)}
    db.add(Job(job_type=JobType.module_generation, step="generate", status=JobStatus.done, document_id=doc.id,
               module_id=module.id, payload=payload))
    return module


@pytest.fixture
def world():
    db = SessionLocal()
    try:
        owner = User(email=f"del-{uuid.uuid4().hex[:8]}@example.com", name="A", hashed_password="x", is_admin=False)
        stranger = User(email=f"del-{uuid.uuid4().hex[:8]}@example.com", name="B", hashed_password="x", is_admin=False)
        db.add_all([owner, stranger])
        db.flush()
        course = Course(name="Deletion", owner_id=owner.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.pdf", status=DocumentStatus.embedded, raw_text="text")
        db.add(doc)
        db.flush()
        rag = _add_module(db, course, doc, "RAG")
        control = _add_module(db, course, doc, "Control", control_of=rag)
        other = _add_module(db, course, doc, "Other RAG")
        db.commit()
        ids = {"owner": owner.id, "stranger": stranger.id, "course": course.id, "doc": doc.id,
               "rag": rag.id, "control": control.id, "other": other.id}
    finally:
        db.close()
    yield ids
    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids["owner"], ids["stranger"]])).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _count(model, **where) -> int:
    db = SessionLocal()
    try:
        return db.query(model).filter_by(**where).count()
    finally:
        db.close()


def _rows_of(module_id) -> int:
    """Everything that hangs off a module, so a leftover row of any kind shows up."""
    db = SessionLocal()
    try:
        components = [c.id for c in db.query(ModuleComponent).filter_by(module_id=module_id)]
        sessions = [s.id for s in db.query(ChatSession).filter_by(module_id=module_id)]
        return (
            db.query(Module).filter_by(id=module_id).count()
            + len(components)
            + db.query(Question).filter(Question.component_id.in_(components)).count()
            + db.query(Evaluation).filter_by(module_id=module_id).count()
            + len(sessions)
            + db.query(ChatMessage).filter(ChatMessage.session_id.in_(sessions)).count()
            + db.query(Job).filter_by(module_id=module_id).count()
        )
    finally:
        db.close()


def test_the_owner_deletes_a_module_and_every_row_that_hangs_off_it(world):
    assert _rows_of(world["other"]) > 0

    response = client.delete(f"/modules/{world['other']}", headers=_headers(world["owner"]))

    assert response.status_code == 200
    assert response.json()["deleted_module_ids"] == [str(world["other"])]
    assert _rows_of(world["other"]) == 0
    assert client.get(f"/modules/{world['other']}", headers=_headers(world["owner"])).status_code == 404


def test_deleting_a_module_removes_the_control_module_paired_with_it(world):
    response = client.delete(f"/modules/{world['rag']}", headers=_headers(world["owner"]))

    assert response.status_code == 200
    assert set(response.json()["deleted_module_ids"]) == {str(world["rag"]), str(world["control"])}
    assert _rows_of(world["rag"]) == 0
    assert _rows_of(world["control"]) == 0
    # Only that pair: the document's other module is untouched.
    assert _rows_of(world["other"]) > 0
    listed = client.get("/modules", params={"document_id": world["doc"]}, headers=_headers(world["owner"])).json()
    assert [m["id"] for m in listed] == [str(world["other"])]


def test_deleting_a_control_module_keeps_the_rag_module_it_was_paired_with(world):
    response = client.delete(f"/modules/{world['control']}", headers=_headers(world["owner"]))

    assert response.status_code == 200
    assert _rows_of(world["control"]) == 0
    assert _rows_of(world["rag"]) > 0


def test_a_failed_control_job_of_the_deleted_module_goes_with_it(world):
    db = SessionLocal()
    try:
        failed = Job(job_type=JobType.module_generation, step="generate", status=JobStatus.failed, document_id=world["doc"],
                     payload={"use_retrieval": False, "paired_module_id": str(world["other"])}, last_error="boom")
        db.add(failed)
        db.commit()
        failed_id = failed.id
    finally:
        db.close()

    assert client.delete(f"/modules/{world['other']}", headers=_headers(world["owner"])).status_code == 200

    assert _count(Job, id=failed_id) == 0


@pytest.mark.parametrize("status", [JobStatus.queued, JobStatus.running])
def test_a_module_whose_control_is_still_being_generated_is_not_deleted(world, status):
    db = SessionLocal()
    try:
        db.add(Job(job_type=JobType.module_generation, step="generate", status=status, document_id=world["doc"],
                   payload={"use_retrieval": False, "paired_module_id": str(world["other"])}))
        db.commit()
    finally:
        db.close()

    response = client.delete(f"/modules/{world['other']}", headers=_headers(world["owner"]))

    assert response.status_code == 409
    assert _rows_of(world["other"]) > 0


def test_another_teacher_cannot_delete_it_and_is_told_nothing_about_it(world):
    attempt = client.delete(f"/modules/{world['rag']}", headers=_headers(world["stranger"]))
    nothing = client.delete(f"/modules/{uuid.uuid4()}", headers=_headers(world["stranger"]))

    assert attempt.status_code == 404
    assert (attempt.status_code, attempt.json()) == (nothing.status_code, nothing.json())
    assert _rows_of(world["rag"]) > 0
    assert _rows_of(world["control"]) > 0


def test_deleting_needs_a_login(world):
    assert client.delete(f"/modules/{world['rag']}").status_code == 401
    assert _rows_of(world["rag"]) > 0


def test_deleting_leaves_the_document_so_it_can_be_generated_again(world):
    assert client.delete(f"/modules/{world['rag']}", headers=_headers(world["owner"])).status_code == 200

    response = client.post("/modules/generate", params={"document_id": world["doc"]}, headers=_headers(world["owner"]))

    assert response.status_code == 200
    assert _count(Document, id=world["doc"]) == 1


def test_generating_again_for_the_same_document_starts_a_new_run_beside_the_finished_module(world):
    """The only guard is a run still in progress: a finished run, whose module is already there, does not
    stop another one, and nothing in the request points at an existing module to replace."""
    owner = _headers(world["owner"])
    before = {m["id"] for m in client.get("/modules", params={"document_id": world["doc"]}, headers=owner).json()}

    first = client.post("/modules/generate", params={"document_id": world["doc"]}, headers=owner)
    assert first.status_code == 200
    # While that run is queued, another is refused.
    assert client.post("/modules/generate", params={"document_id": world["doc"]}, headers=owner).status_code == 409

    # The worker finishes it by adding a module, as run_module_generation_step does.
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(first.json()["job_id"]))
        course = db.get(Course, world["course"])
        doc = db.get(Document, world["doc"])
        module = _add_module(db, course, doc, "Second run")
        job.module_id, job.status = module.id, JobStatus.done
        db.commit()
    finally:
        db.close()

    second = client.post("/modules/generate", params={"document_id": world["doc"]}, headers=owner)
    assert second.status_code == 200
    assert second.json()["job_id"] != first.json()["job_id"]
    after = {m["id"] for m in client.get("/modules", params={"document_id": world["doc"]}, headers=owner).json()}
    assert before < after and len(after) == len(before) + 1



def test_deleting_module_removes_control_after_jobs_expire(world):
    with SessionLocal() as db:
        rag = db.get(Module, world["rag"])
        control = db.get(Module, world["control"])
        rag.generation_payload = {}
        control.generation_payload = {"use_retrieval": False, "paired_module_id": str(rag.id)}
        control.paired_module_id = rag.id
        db.query(Job).filter(Job.module_id.in_([rag.id, control.id])).delete(synchronize_session=False)
        db.commit()
    response = client.delete(f"/modules/{world['rag']}", headers=_headers(world["owner"]))
    assert response.status_code == 200, response.text
    assert set(response.json()["deleted_module_ids"]) == {str(world["rag"]), str(world["control"])}
    assert _rows_of(world["rag"]) == _rows_of(world["control"]) == 0
