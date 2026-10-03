import uuid

from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Chunk, Course, Document, Module, User, UserRole

client = TestClient(app)


def _make_authed_user() -> tuple[str, uuid.UUID]:
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        user = User(
            email=f"del-{suffix}@example.com", name="Test", hashed_password="x", role=UserRole.teacher, moodle_user_id=4242
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return create_access_token(user.id), user.id
    finally:
        db.close()


def _make_course(token: str) -> str:
    course = client.post(
        "/courses", json={"name": f"Course {uuid.uuid4().hex[:8]}"}, headers={"Authorization": f"Bearer {token}"}
    ).json()
    return course["id"]


def _make_document_with_children(course_id: str) -> tuple[str, str, str]:
    """Direct DB setup: a document with a chunk and a module under the given
    course, so deletion tests can assert the whole subtree actually goes
    away (not just the top-level row)."""
    db = SessionLocal()
    try:
        document = Document(course_id=uuid.UUID(course_id), filename="notes.pdf")
        db.add(document)
        db.flush()
        chunk = Chunk(document_id=document.id, course_id=uuid.UUID(course_id), chunk_index=0, content="hello")
        db.add(chunk)
        module = Module(document_id=document.id, course_id=uuid.UUID(course_id), title="M1")
        db.add(module)
        db.commit()
        return str(document.id), str(chunk.id), str(module.id)
    finally:
        db.close()


def _row_exists(model, row_id: str) -> bool:
    db = SessionLocal()
    try:
        return db.get(model, uuid.UUID(row_id)) is not None
    finally:
        db.close()


class TestUnlinkMoodle:
    def test_clears_the_link(self):
        token, _ = _make_authed_user()
        course_id = _make_course(token)
        headers = {"Authorization": f"Bearer {token}"}
        # Unique per run: moodle_course_id is unique table-wide and the test DB persists.
        moodle_id = 1_000 + uuid.uuid4().int % 1_000_000_000
        client.post(f"/courses/{course_id}/link-moodle", json={"moodle_course_id": moodle_id}, headers=headers)

        response = client.delete(f"/courses/{course_id}/link-moodle", headers=headers)
        assert response.status_code == 200
        assert response.json()["moodle_course_id"] is None

        courses = client.get("/courses", headers=headers).json()
        assert next(c for c in courses if c["id"] == course_id)["moodle_course_id"] is None

    def test_refuses_for_a_course_you_do_not_own(self):
        token_owner, _ = _make_authed_user()
        token_other, _ = _make_authed_user()
        course_id = _make_course(token_owner)

        response = client.delete(
            f"/courses/{course_id}/link-moodle", headers={"Authorization": f"Bearer {token_other}"}
        )
        assert response.status_code == 404


class TestDeleteCourse:
    def test_deletes_the_course_and_everything_under_it(self):
        token, _ = _make_authed_user()
        course_id = _make_course(token)
        document_id, chunk_id, module_id = _make_document_with_children(course_id)

        response = client.delete(f"/courses/{course_id}", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

        assert not _row_exists(Course, course_id)
        assert not _row_exists(Document, document_id)
        assert not _row_exists(Chunk, chunk_id)
        assert not _row_exists(Module, module_id)

    def test_refuses_for_a_course_you_do_not_own(self):
        token_owner, _ = _make_authed_user()
        token_other, _ = _make_authed_user()
        course_id = _make_course(token_owner)

        response = client.delete(f"/courses/{course_id}", headers={"Authorization": f"Bearer {token_other}"})
        assert response.status_code == 404
        assert _row_exists(Course, course_id)


class TestDeleteDocument:
    def test_deletes_the_document_and_its_chunks_and_modules(self):
        token, _ = _make_authed_user()
        course_id = _make_course(token)
        document_id, chunk_id, module_id = _make_document_with_children(course_id)

        response = client.delete(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

        assert not _row_exists(Document, document_id)
        assert not _row_exists(Chunk, chunk_id)
        assert not _row_exists(Module, module_id)
        # The course itself must survive -- only the document subtree goes.
        assert _row_exists(Course, course_id)

    def test_refuses_for_a_document_in_a_course_you_do_not_own(self):
        token_owner, _ = _make_authed_user()
        token_other, _ = _make_authed_user()
        course_id = _make_course(token_owner)
        document_id, _, _ = _make_document_with_children(course_id)

        response = client.delete(
            f"/documents/{document_id}", headers={"Authorization": f"Bearer {token_other}"}
        )
        assert response.status_code == 404
        assert _row_exists(Document, document_id)
