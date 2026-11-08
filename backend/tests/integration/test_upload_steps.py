"""The upload in three steps: ask where to send the file, send it, report it sent.

Between the first and the last step a document exists without its file. These tests pin what that
document may and may not do: it is invisible in the course, it cannot be completed twice or by
someone else, a race with another upload is settled at completion, and one never completed is
removed by the worker's hourly clean-up together with whatever part of its file arrived."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.config import settings
from app.db import SessionLocal
from app.jobs.maintenance import purge_old_records
from app.main import app
from app.models import Course, Document, DocumentStatus, Job, User
from app.storage import upload_key, upload_store

client = TestClient(app)
BODY = b"<p>Khai niem A la mot y quan trong.</p>"


@pytest.fixture(autouse=True)
def isolated_upload_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))


@pytest.fixture
def world():
    db = SessionLocal()
    try:
        users = [User(email=f"steps-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x",
                      is_admin=False) for _ in range(2)]
        db.add_all(users)
        db.flush()
        course = Course(name="Upload steps", owner_id=users[0].id)
        db.add(course)
        db.commit()
        ids = {"owner": users[0].id, "stranger": users[1].id, "course": course.id}
    finally:
        db.close()
    yield {
        **ids,
        "owner_headers": {"Authorization": f"Bearer {create_access_token(ids['owner'])}"},
        "stranger_headers": {"Authorization": f"Bearer {create_access_token(ids['stranger'])}"},
    }
    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids["owner"], ids["stranger"]])).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _start(world, filename="a.html", size=len(BODY)):
    response = client.post(
        "/documents/uploads", params={"course_id": world["course"]},
        json={"filename": filename, "size": size}, headers=world["owner_headers"],
    )
    assert response.status_code == 200, response.text
    return response.json()


def _listed(world):
    return client.get("/documents", params={"course_id": world["course"]}, headers=world["owner_headers"]).json()


def test_a_document_waiting_for_its_file_is_not_listed_until_completed(world):
    started = _start(world)
    assert started["upload"] == {"method": "PUT", "url": f"/documents/{started['document_id']}/content", "fields": {}}
    assert _listed(world) == []

    assert client.put(started["upload"]["url"], content=BODY, headers=world["owner_headers"]).status_code == 200
    assert _listed(world) == [], "sent but not reported sent: still not a document of the course"

    done = client.post(f"/documents/{started['document_id']}/complete", headers=world["owner_headers"])
    assert done.status_code == 200, done.text
    assert [d["id"] for d in _listed(world)] == [started["document_id"]]
    assert client.put(started["upload"]["url"], content=BODY, headers=world["owner_headers"]).status_code == 409


def test_another_user_can_neither_send_nor_complete_the_file(world):
    started = _start(world)

    put = client.put(started["upload"]["url"], content=BODY, headers=world["stranger_headers"])
    complete = client.post(f"/documents/{started['document_id']}/complete", headers=world["stranger_headers"])

    assert put.status_code == 404
    assert complete.status_code == 404
    assert upload_store().size(upload_key(started["document_id"], "a.html")) is None


def test_two_uploads_started_together_cannot_both_be_processed(world):
    # Both pass the first check, since nothing is being processed yet; completion decides.
    first, second = _start(world, "a.html"), _start(world, "b.html")
    for started in (first, second):
        client.put(started["upload"]["url"], content=BODY, headers=world["owner_headers"])

    assert client.post(f"/documents/{first['document_id']}/complete", headers=world["owner_headers"]).status_code == 200
    refused = client.post(f"/documents/{second['document_id']}/complete", headers=world["owner_headers"])

    assert refused.status_code == 409
    with SessionLocal() as db:
        assert db.get(Document, second["document_id"]) is None, "the refused one is gone, to be uploaded again"
        assert db.query(Job).filter(Job.document_id == second["document_id"]).count() == 0
    assert upload_store().size(upload_key(second["document_id"], "b.html")) is None


def test_an_upload_never_completed_is_removed_after_an_hour_with_its_file(world):
    stale, fresh = _start(world, "old.html"), _start(world, "new.html")
    for started in (stale, fresh):
        client.put(started["upload"]["url"], content=BODY, headers=world["owner_headers"])
    with SessionLocal() as db:
        db.get(Document, stale["document_id"]).created_at = datetime.now(UTC) - timedelta(hours=1, minutes=1)
        db.commit()

    removed = purge_old_records()

    assert removed["abandoned_uploads"] >= 1
    with SessionLocal() as db:
        assert db.get(Document, stale["document_id"]) is None
        assert db.get(Document, fresh["document_id"]).status == DocumentStatus.awaiting_upload
    assert upload_store().size(upload_key(stale["document_id"], "old.html")) is None
    assert upload_store().size(upload_key(fresh["document_id"], "new.html")) == len(BODY)


def test_an_empty_file_is_refused(world):
    started = _start(world)

    response = client.put(started["upload"]["url"], content=b"", headers=world["owner_headers"])

    assert response.status_code == 422
    assert client.post(f"/documents/{started['document_id']}/complete",
                       headers=world["owner_headers"]).status_code == 409
