"""On ECS the API and the worker are separate tasks with separate disks. The browser sends the file
straight to S3 through a presigned POST; the worker reads it from there. Nothing is left behind on
either side, and the file never passes through the API."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.auth.security import create_access_token
from app.config import settings
from app.db import SessionLocal
from app.ingestion.document_pipeline import run_document_pipeline_step
from app.main import app
from app.models import Chunk, Course, Document, DocumentStatus, Job, User
from tests.job_helpers import claimed
from tests.unit.test_upload_store import FakeS3
from tests.upload_helpers import upload_document

client = TestClient(app)


@pytest.fixture
def s3(monkeypatch, tmp_path):
    fake = FakeS3()
    monkeypatch.setattr(settings, "upload_store", "s3")
    monkeypatch.setattr(settings, "upload_s3_bucket", "uploads-bucket")
    monkeypatch.setattr(storage, "_s3_client", lambda: fake)
    # Where the local store would write: nothing may land here while S3 is in use.
    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path / "shared"))
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(storage.tempfile, "gettempdir", lambda: str(staging))
    return fake, tmp_path


@pytest.fixture
def world():
    db = SessionLocal()
    try:
        user = User(email=f"s3-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="S3 upload", owner_id=user.id)
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


BODY = b"<p>Khai niem A la mot y quan trong can duoc giu lai khi chia tai lieu.</p>" * 5


def _headers(world):
    return {"Authorization": f"Bearer {world['token']}"}


def _upload(world, name="Bai Giang.HTML", s3=None):
    return upload_document(client, world["course"], name, BODY, _headers(world), s3=s3)


def _run_until_done(job_id):
    for _ in range(3):
        db = SessionLocal()
        try:
            run_document_pipeline_step(claimed(db, job_id))
        finally:
            db.close()


def test_the_worker_reads_the_upload_from_s3_and_keeps_it_when_embedded(s3, world):
    fake, tmp_path = s3

    response = _upload(world, s3=fake)

    assert response.status_code == 200, response.text
    doc_id = response.json()["id"]
    assert list(fake.objects) == [("uploads-bucket", f"uploads/{doc_id}.html")]
    assert not (tmp_path / "shared").exists(), "the API's disk is not shared with the worker"
    assert list((tmp_path / "staging").iterdir()) == [], "the file never passes through the API"
    db = SessionLocal()
    try:
        assert db.get(Job, response.json()["job_id"]).payload == {"storage_key": f"{doc_id}.html"}
    finally:
        db.close()

    _run_until_done(response.json()["job_id"])

    db = SessionLocal()
    try:
        assert db.get(Document, doc_id).status == DocumentStatus.embedded
        assert db.query(Chunk).filter(Chunk.document_id == doc_id).count() > 0
    finally:
        db.close()
    # Kept: a study copies it as the file raters check the content against.
    assert list(fake.objects) == [("uploads-bucket", f"uploads/{doc_id}.html")]
    assert list((tmp_path / "staging").iterdir()) == [], "the worker's copy is removed once read"


def test_the_presigned_post_is_capped_at_the_declared_size(s3, world):
    fake, _ = s3

    response = client.post(
        "/documents/uploads", params={"course_id": world["course"]},
        json={"filename": "lecture.mp4", "size": 1234}, headers=_headers(world),
    )

    assert response.status_code == 200, response.text
    target = response.json()["upload"]
    assert target["method"] == "POST"
    doc_id = response.json()["document_id"]
    assert target["fields"]["key"] == f"uploads/{doc_id}.mp4"
    assert fake.policies[f"uploads/{doc_id}.mp4"] == ("uploads-bucket", 1234)
    # Without S3 the API would take the file itself; with S3 that route does not exist.
    assert client.put(f"/documents/{doc_id}/content", content=b"x", headers=_headers(world)).status_code == 404


def test_completing_before_the_file_arrived_keeps_the_document_waiting(s3, world):
    fake, _ = s3
    started = client.post(
        "/documents/uploads", params={"course_id": world["course"]},
        json={"filename": "a.html", "size": len(BODY)}, headers=_headers(world),
    ).json()

    early = client.post(f"/documents/{started['document_id']}/complete", headers=_headers(world))
    assert early.status_code == 409

    fake.browser_post(started["upload"], BODY)
    done = client.post(f"/documents/{started['document_id']}/complete", headers=_headers(world))
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "uploaded"
    again = client.post(f"/documents/{started['document_id']}/complete", headers=_headers(world))
    assert again.status_code == 409, "a second complete must not queue the pipeline twice"


def test_a_stored_file_over_the_limit_is_refused_and_removed(s3, world, monkeypatch):
    from app.api import documents

    fake, _ = s3
    started = client.post(
        "/documents/uploads", params={"course_id": world["course"]},
        json={"filename": "a.html", "size": 10}, headers=_headers(world),
    ).json()
    # As if the policy allowed more than the limit now does.
    fake.objects[("uploads-bucket", started["upload"]["fields"]["key"])] = b"x" * 11
    monkeypatch.setattr(documents, "MAX_UPLOAD_SIZE_BYTES", 10)

    response = client.post(f"/documents/{started['document_id']}/complete", headers=_headers(world))

    assert response.status_code == 413
    assert fake.objects == {}
    with SessionLocal() as db:
        assert db.get(Document, started["document_id"]) is None


def test_deleting_a_document_removes_its_file_from_s3(s3, world):
    fake, _ = s3
    doc_id = _upload(world, s3=fake).json()["id"]
    assert fake.objects

    response = client.delete(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {world['token']}"})

    assert response.status_code == 200, response.text
    assert fake.objects == {}


def test_an_unreachable_s3_at_completion_leaves_the_document_waiting(s3, world, monkeypatch):
    fake, _ = s3
    started = client.post(
        "/documents/uploads", params={"course_id": world["course"]},
        json={"filename": "a.html", "size": len(BODY)}, headers=_headers(world),
    ).json()
    fake.browser_post(started["upload"], BODY)

    def unreachable(**_):
        raise ConnectionError("S3 unreachable")

    monkeypatch.setattr(fake, "head_object", unreachable)
    with pytest.raises(ConnectionError):
        client.post(f"/documents/{started['document_id']}/complete", headers=_headers(world))

    with SessionLocal() as db:
        assert db.get(Document, started["document_id"]).status == DocumentStatus.awaiting_upload
        assert db.query(Job).filter(Job.document_id == started["document_id"]).count() == 0


def test_deleting_a_course_removes_the_files_of_its_unfinished_documents(s3, world):
    fake, _ = s3
    _upload(world, s3=fake)
    assert fake.objects

    response = client.delete(f"/courses/{world['course']}", headers={"Authorization": f"Bearer {world['token']}"})

    assert response.status_code == 200, response.text
    assert fake.objects == {}
