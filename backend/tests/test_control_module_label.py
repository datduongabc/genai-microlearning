import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import (
    Chunk,
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
        user = User(email=f"control-{uuid.uuid4()}@example.com", name="T", hashed_password="x", role=UserRole.teacher, moodle_user_id=4242)
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


def test_detail_citations_resolve_within_document_and_require_owner(modules):
    user, course, doc, rag, control = modules
    saved = [{"claim": "Grounded", "locator": "lesson.pdf, trang 2"},
             {"claim": "Missing", "locator": "other.pdf, trang 1"},
             {"claim": "Ambiguous", "locator": "duplicate"}]
    with SessionLocal() as db:
        other_doc = Document(course_id=course, filename="other.pdf")
        db.add(other_doc)
        db.flush()
        db.add_all([
            ModuleComponent(module_id=rag, type=ComponentType.content,
                            content={"markdown": "Lesson", "claim_citations": saved}),
            Chunk(course_id=course, document_id=doc, chunk_index=0,
                  content="Original source paragraph", source_ref=saved[0]["locator"]),
            Chunk(course_id=course, document_id=other_doc.id, chunk_index=0,
                  content="Not this document", source_ref=saved[1]["locator"]),
            *[Chunk(course_id=course, document_id=doc, chunk_index=i,
                    content="Duplicate source", source_ref="duplicate") for i in (1, 2)],
        ])
        db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(str(user))}"}
    response = client.get(f"/modules/{rag}", headers=headers)
    assert response.status_code == 200
    citations = response.json()["claim_citations"]
    assert [(c["claim"], c["locator"], c["chunk_content"], c["resolution"]) for c in citations] == [
        ("Grounded", "lesson.pdf, trang 2", "Original source paragraph", "resolved"),
        ("Missing", "other.pdf, trang 1", None, "missing"),
        ("Ambiguous", "duplicate", None, "ambiguous"),
    ]
    assert client.get(f"/modules/{control}", headers=headers).json()["claim_citations"] is None
    with SessionLocal() as db:
        content = db.query(ModuleComponent).filter(ModuleComponent.module_id == rag).one()
        content.content = {"markdown": "Legacy"}
        db.commit()
    assert client.get(f"/modules/{rag}", headers=headers).json()["claim_citations"] is None
    assert client.get(f"/modules/{rag}").status_code == 401
    stranger = {"Authorization": f"Bearer {create_access_token(str(uuid.uuid4()))}"}
    assert client.get(f"/modules/{rag}", headers=stranger).status_code == 401
    # A real authenticated non-owner must also be refused.
    with SessionLocal() as db:
        outsider = User(email=f"outsider-{uuid.uuid4()}@example.com", name="Other",
                        hashed_password="x", role=UserRole.teacher, moodle_user_id=4242)
        db.add(outsider)
        db.commit()
        outsider_id = outsider.id
    try:
        headers = {"Authorization": f"Bearer {create_access_token(str(outsider_id))}"}
        assert client.get(f"/modules/{rag}", headers=headers).status_code == 404
    finally:
        with SessionLocal() as db:
            db.query(User).filter(User.id == outsider_id).delete()
            db.commit()



def test_detail_formats_structured_citation_without_changing_resolution(modules):
    import json
    user, course, doc, rag, _ = modules
    locator = json.dumps({"spans": [{"page": 9, "heading": None, "extraction_method": "text_layer"},
                                  {"page": 10, "heading": "Search", "extraction_method": "text_layer"}]})
    with SessionLocal() as db:
        filename = db.get(Document, doc).filename
        db.add(ModuleComponent(module_id=rag, type=ComponentType.content,
                               content={"markdown": "Lesson", "claim_citations": [{"claim": "Fact", "locator": locator}]}))
        db.add(Chunk(course_id=course, document_id=doc, chunk_index=0, content="Original", source_ref=locator))
        db.commit()
    response = client.get(f"/modules/{rag}", headers={"Authorization": f"Bearer {create_access_token(str(user))}"})
    citation = response.json()["claim_citations"][0]
    assert citation["locator"] == f"{filename} · trang 9–10 · Search"
    assert citation["chunk_content"] == "Original"
    assert citation["resolution"] == "resolved"
