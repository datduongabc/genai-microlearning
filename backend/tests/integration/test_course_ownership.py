import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Course, Document, DocumentStatus, Job, JobStatus, JobType, User
from tests.auth_helpers import create_and_login
from tests.upload_helpers import upload_document

client = TestClient(app)


def _create_and_login(email: str) -> str:
    return create_and_login(client, email)["access_token"]


def test_user_cannot_see_or_use_another_users_course():
    suffix = uuid.uuid4().hex[:8]
    token_owner = _create_and_login(f"owner-{suffix}@example.com")
    token_other = _create_and_login(f"other-{suffix}@example.com")

    course = client.post(
        "/courses", json={"name": f"Course {suffix}"}, headers={"Authorization": f"Bearer {token_owner}"}
    ).json()
    course_id = course["id"]

    # A course must not appear in another user's course list.
    other_courses = client.get("/courses", headers={"Authorization": f"Bearer {token_other}"}).json()
    assert all(c["id"] != course_id for c in other_courses)

    # ...and that user must not be able to upload into it, even knowing its id.
    response = upload_document(
        client, course_id, "x.html", b"<p>hello</p>", {"Authorization": f"Bearer {token_other}"},
    )
    assert response.status_code == 404

    # The owner still sees and can use their own course.
    owner_courses = client.get("/courses", headers={"Authorization": f"Bearer {token_owner}"}).json()
    assert any(c["id"] == course_id for c in owner_courses)


def test_only_the_owner_renames_a_course_and_the_name_is_cleaned():
    suffix = uuid.uuid4().hex[:8]
    owner = {"Authorization": f"Bearer {_create_and_login(f'rename-owner-{suffix}@example.com')}"}
    other = {"Authorization": f"Bearer {_create_and_login(f'rename-other-{suffix}@example.com')}"}
    course_id = client.post("/courses", json={"name": "Cũ"}, headers=owner).json()["id"]

    assert client.patch(f"/courses/{course_id}", json={"name": "Lạ"}, headers=other).status_code == 404
    assert client.patch(f"/courses/{course_id}", json={"name": "   "}, headers=owner).status_code == 422
    renamed = client.patch(f"/courses/{course_id}", json={"name": "  Hệ   điều hành "}, headers=owner)
    assert renamed.status_code == 200 and renamed.json()["name"] == "Hệ điều hành"
    assert [c["name"] for c in client.get("/courses", headers=owner).json() if c["id"] == course_id] == ["Hệ điều hành"]


@pytest.fixture
def world():
    db = SessionLocal()
    try:
        user = User(
            email=f"gen-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False
        )
        db.add(user)
        db.flush()
        course = Course(name="Generating flag", owner_id=user.id)
        db.add(course)
        db.commit()
        ids = {"user": user.id, "course": course.id}
    finally:
        db.close()

    yield {**ids, "token": create_access_token(ids["user"])}

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _add_document(course_id, filename: str, job_status: JobStatus | None, job_type=JobType.module_generation):
    """A ready document, optionally with one job in the given state."""
    db = SessionLocal()
    try:
        doc = Document(course_id=course_id, filename=filename, status=DocumentStatus.embedded, raw_text="text")
        db.add(doc)
        db.flush()
        if job_status is not None:
            db.add(Job(job_type=job_type, step="generate", status=job_status, document_id=doc.id))
        db.commit()
        return doc.id
    finally:
        db.close()


def _generating_by_filename(world) -> dict[str, bool]:
    response = client.get(
        "/documents", params={"course_id": world["course"]}, headers={"Authorization": f"Bearer {world['token']}"}
    )
    assert response.status_code == 200
    return {d["filename"]: d["generating"] for d in response.json()}


def test_a_document_with_a_queued_or_running_generation_is_flagged(world):
    _add_document(world["course"], "queued.txt", JobStatus.queued)
    _add_document(world["course"], "running.txt", JobStatus.running)

    flags = _generating_by_filename(world)

    assert flags == {"queued.txt": True, "running.txt": True}


def test_finished_failed_or_absent_generations_are_not_flagged(world):
    _add_document(world["course"], "done.txt", JobStatus.done)
    _add_document(world["course"], "failed.txt", JobStatus.failed)
    _add_document(world["course"], "never.txt", None)

    flags = _generating_by_filename(world)

    assert flags == {"done.txt": False, "failed.txt": False, "never.txt": False}


def test_a_running_document_pipeline_job_is_not_a_generation(world):
    _add_document(world["course"], "pipeline.txt", JobStatus.running, job_type=JobType.document_pipeline)

    assert _generating_by_filename(world) == {"pipeline.txt": False}
