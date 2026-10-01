import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import (
    ComponentType,
    Course,
    Document,
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
    User,
    UserRole,
)
from app.moodle import client as moodle_client

client = TestClient(app)


@pytest.fixture
def modules():
    with SessionLocal() as db:
        user = User(email=f"control-{uuid.uuid4()}@example.com", name="T", hashed_password="x", role=UserRole.teacher)
        db.add(user)
        db.flush()
        course = Course(name="Study", owner_id=user.id, moodle_course_id=123)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="lesson.pdf")
        db.add(doc)
        db.flush()
        rag = Module(course_id=course.id, document_id=doc.id, title="RAG")
        control = Module(course_id=course.id, document_id=doc.id, title="Control")
        db.add_all([rag, control])
        db.flush()
        db.add(ModuleComponent(module_id=control.id, type=ComponentType.content, content={"markdown": "Lesson"}))
        db.add(Job(job_type=JobType.module_generation, step="generate", status=JobStatus.done,
                   document_id=doc.id, module_id=control.id,
                   payload={"use_retrieval": False, "paired_module_id": str(rag.id)}))
        db.commit()
        ids = (user.id, course.id, doc.id, rag.id, control.id)
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids[1]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids[0]).delete(synchronize_session=False)
        db.commit()


def test_list_and_detail_identify_control_and_regular_modules(modules):
    user, _, doc, rag, control = modules
    headers = {"Authorization": f"Bearer {create_access_token(str(user))}"}
    response = client.get(f"/modules?document_id={doc}", headers=headers)
    assert response.status_code == 200
    assert {m["id"]: m["is_control"] for m in response.json()} == {str(rag): False, str(control): True}
    for module_id, expected in ((rag, False), (control, True)):
        response = client.get(f"/modules/{module_id}", headers=headers)
        assert response.status_code == 200
        assert response.json()["is_control"] is expected


def test_control_publish_is_rejected_before_any_moodle_call(modules, monkeypatch):
    user, _, _, _, control = modules
    call = Mock(return_value=42)
    monkeypatch.setattr(moodle_client, "call", call)
    response = client.post(f"/modules/{control}/publish-to-moodle",
                           headers={"Authorization": f"Bearer {create_access_token(str(user))}"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Mô-đun đối chứng (không RAG) chỉ dùng cho nghiên cứu, không được đẩy lên Moodle."
    call.assert_not_called()
