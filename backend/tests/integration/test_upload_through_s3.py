"""On ECS the API and the worker are separate tasks with separate disks. A document uploaded to
one must be read by the other through S3, and leave nothing behind on either side."""

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
from tests.unit.test_upload_store import FakeS3

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


def _upload(world, name="Bai Giang.HTML"):
    body = b"<p>Khai niem A la mot y quan trong can duoc giu lai khi chia tai lieu.</p>" * 5
    return client.post(
        "/documents", params={"course_id": world["course"]},
        files={"file": (name, body, "text/html")},
        headers={"Authorization": f"Bearer {world['token']}"},
    )


def _run_until_done(job_id):
    for _ in range(3):
        db = SessionLocal()
        try:
            run_document_pipeline_step(db.get(Job, job_id))
        finally:
            db.close()


def test_the_worker_reads_the_upload_from_s3_and_removes_it_when_embedded(s3, world):
    fake, tmp_path = s3

    response = _upload(world)

    assert response.status_code == 200, response.text
    doc_id = response.json()["id"]
    assert list(fake.objects) == [("uploads-bucket", f"uploads/{doc_id}.html")]
    assert not (tmp_path / "shared").exists(), "the API's disk is not shared with the worker"
    assert list((tmp_path / "staging").iterdir()) == [], "the staged copy must not pile up"
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
    assert fake.objects == {}, "an embedded document's file has no further use"
    assert list((tmp_path / "staging").iterdir()) == [], "the worker's copy is removed once read"


def test_deleting_a_document_removes_its_file_from_s3(s3, world):
    fake, _ = s3
    doc_id = _upload(world).json()["id"]
    assert fake.objects

    response = client.delete(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {world['token']}"})

    assert response.status_code == 200, response.text
    assert fake.objects == {}


def test_a_failed_s3_put_leaves_no_document_behind(s3, world, monkeypatch):
    fake, tmp_path = s3

    def unreachable(*_):
        raise ConnectionError("S3 unreachable")

    monkeypatch.setattr(fake, "upload_file", unreachable)

    with pytest.raises(ConnectionError):
        _upload(world)

    db = SessionLocal()
    try:
        assert db.query(Document).filter(Document.course_id == world["course"]).count() == 0
    finally:
        db.close()
    assert list((tmp_path / "staging").iterdir()) == []


def test_deleting_a_course_removes_the_files_of_its_unfinished_documents(s3, world):
    fake, _ = s3
    _upload(world)
    assert fake.objects

    response = client.delete(f"/courses/{world['course']}", headers={"Authorization": f"Bearer {world['token']}"})

    assert response.status_code == 200, response.text
    assert fake.objects == {}
