"""A document's generated images, voice and videos are files outside the database, so deleting the document
or its course has to delete them itself: the cascade only reaches rows."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Course, Document, User
from app.models.media import Video
from app.storage import media_store

client = TestClient(app)
KEYS = ["v/final.mp4", "v/scene-1.png", "v/scene-2.png", "v/voice-1.mp3"]


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.upload_storage_dir", str(tmp_path))
    store = media_store()
    with SessionLocal() as db:
        user = User(email=f"media-del-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x")
        db.add(user)
        db.flush()
        course = Course(name="C", owner_id=user.id)
        db.add(course)
        db.flush()
        document = Document(course_id=course.id, filename="notes.pdf")
        db.add(document)
        db.flush()
        db.add(Video(course_id=course.id, document_id=document.id, output_language="vi", model_alias="gemini-flash",
                     voice_alias="voice-heygen", image_alias="image-nano-banana", video_key=KEYS[0],
                     scenes=[{"image_key": KEYS[1], "voice_key": KEYS[3]}, {"image_key": KEYS[2]}]))
        db.commit()
        ids = {"token": create_access_token(user.id), "document": document.id, "course": course.id, "user": user.id}
    for key in KEYS:
        source = tmp_path / "src"
        source.write_bytes(b"x")
        store.save(source, key)  # moves the source, so each key gets a fresh one
    assert all(store.size(key) == 1 for key in KEYS)
    yield store, ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.owner_id == ids["user"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()


def test_deleting_a_document_removes_its_media_files(world):
    store, ids = world
    response = client.delete(f"/documents/{ids['document']}", headers={"Authorization": f"Bearer {ids['token']}"})
    assert response.status_code == 200
    assert [store.size(key) for key in KEYS] == [None] * len(KEYS)


def test_deleting_a_course_removes_its_media_files(world):
    store, ids = world
    response = client.delete(f"/courses/{ids['course']}", headers={"Authorization": f"Bearer {ids['token']}"})
    assert response.status_code == 200
    assert [store.size(key) for key in KEYS] == [None] * len(KEYS)
