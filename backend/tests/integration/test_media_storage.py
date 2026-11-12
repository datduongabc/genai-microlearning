"""A document's generated images and videos are files outside the database, so deleting the document
has to delete them itself: the cascade only reaches rows."""

import uuid

from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Course, Document, User
from app.models.media import Video
from app.storage import media_store

client = TestClient(app)


def test_deleting_a_document_removes_its_media_files(tmp_path, monkeypatch):
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
        video = Video(course_id=course.id, document_id=document.id, output_language="vi", model_alias="gemini-fast",
                      voice_alias="voice-heygen", image_alias="image-nano-banana", video_key="v/final.mp4",
                      scenes=[{"image_key": "v/scene-1.png"}, {"image_key": "v/scene-2.png"}])
        db.add(video)
        db.commit()
        token, document_id, user_id = create_access_token(user.id), document.id, user.id
    keys = ["v/final.mp4", "v/scene-1.png", "v/scene-2.png"]
    for key in keys:
        source = tmp_path / "src"
        source.write_bytes(b"x")
        store.save(source, key)  # moves the source, so each key gets a fresh one
    assert all(store.size(key) == 1 for key in keys)

    response = client.delete(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert [store.size(key) for key in keys] == [None, None, None]
    with SessionLocal() as db:
        db.query(Course).filter(Course.owner_id == user_id).delete(synchronize_session=False)
        db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
        db.commit()
