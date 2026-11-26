"""The lecturer picks a module's concept from the planner's list, and the module is built on it."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.rate_limit import limiter
from app.auth.security import create_access_token
from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.generation.planner import Concept, PlannerOutput
from app.main import app
from app.models import Course, Document, DocumentStatus, Job, JobType, User

client = TestClient(app)

PLANNED = [Concept(name="Hàm băm", priority=2, summary="Ánh xạ khóa vào chỉ số."),
           Concept(name="Bảng băm", priority=1, summary="Cấu trúc lưu cặp khóa và giá trị."),
           Concept(name="Xung đột", priority=3, summary="Hai khóa cùng một chỉ số.")]


@pytest.fixture(autouse=True)
def no_limits(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)


@pytest.fixture
def planner(monkeypatch):
    calls = []

    def run(text, output_language="vi", custom_instructions=""):
        calls.append((output_language, custom_instructions))
        return PlannerOutput(concepts=PLANNED)

    monkeypatch.setattr(pipeline, "run_planner", run)
    return calls


@pytest.fixture
def lecturer():
    with SessionLocal() as db:
        user = User(email=f"concepts-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Concepts", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.pdf", status=DocumentStatus.embedded, raw_text="Bảng băm ...")
        db.add(doc)
        db.commit()
        ids = {"user": user.id, "course": course.id, "doc": doc.id,
               "headers": {"Authorization": f"Bearer {create_access_token(str(user.id))}"}}
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids["course"]).delete()
        db.query(User).filter(User.id == ids["user"]).delete()
        db.commit()


def _concepts(lecturer, language="vi", headers=None):
    return client.get("/modules/concepts", params={"document_id": str(lecturer["doc"]), "output_language": language},
                      headers=headers or lecturer["headers"])


def test_the_concepts_come_most_important_first_and_are_asked_for_once(lecturer, planner):
    first = _concepts(lecturer)
    again = _concepts(lecturer)

    assert first.status_code == 200
    assert [c["name"] for c in first.json()] == ["Bảng băm", "Hàm băm", "Xung đột"]
    assert first.json()[0] == {"name": "Bảng băm", "summary": "Cấu trúc lưu cặp khóa và giá trị."}
    assert again.json() == first.json()
    # Without the lecturer's guidance: typing it in the dialog must not ask the planner again.
    assert planner == [("vi", "")]


def test_another_language_asks_the_planner_in_that_language(lecturer, planner):
    _concepts(lecturer, "vi")
    _concepts(lecturer, "en")
    assert planner == [("vi", ""), ("en", "")]


def test_a_planner_that_fails_answers_503_for_the_dialog_to_generate_without_a_pick(lecturer, monkeypatch):
    def down(*_a, **_k):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(pipeline, "run_planner", down)
    response = _concepts(lecturer)
    assert response.status_code == 503
    assert "tự chọn" in response.json()["detail"]


def test_another_lecturers_document_lists_nothing(lecturer, planner):
    with SessionLocal() as db:
        other = User(email=f"other-{uuid.uuid4().hex[:8]}@example.com", name="O", hashed_password="x", is_admin=False)
        db.add(other)
        db.commit()
        other_id = other.id
    try:
        response = _concepts(lecturer, headers={"Authorization": f"Bearer {create_access_token(str(other_id))}"})
        assert response.status_code == 404
        assert planner == []
    finally:
        with SessionLocal() as db:
            db.query(User).filter(User.id == other_id).delete()
            db.commit()


def _job_payload(lecturer) -> dict:
    with SessionLocal() as db:
        job = db.query(Job).filter(Job.document_id == lecturer["doc"], Job.job_type == JobType.module_generation).one()
        return job.payload


def test_the_picked_concept_goes_to_the_worker_with_the_job(lecturer):
    response = client.post("/modules/generate", params={"document_id": str(lecturer["doc"])}, headers=lecturer["headers"],
                           json={"concept": {"name": " Xung đột ", "summary": "Hai khóa cùng một chỉ số."}})

    assert response.status_code == 200
    assert _job_payload(lecturer)["concept"] == {"name": "Xung đột", "summary": "Hai khóa cùng một chỉ số."}


def test_a_generation_without_a_pick_leaves_the_choice_to_the_worker(lecturer):
    response = client.post("/modules/generate", params={"document_id": str(lecturer["doc"])}, headers=lecturer["headers"],
                           json={})

    assert response.status_code == 200
    assert "concept" not in _job_payload(lecturer)


def test_a_concept_without_a_name_is_refused(lecturer):
    response = client.post("/modules/generate", params={"document_id": str(lecturer["doc"])}, headers=lecturer["headers"],
                           json={"concept": {"name": "  ", "summary": "x"}})
    assert response.status_code == 422
