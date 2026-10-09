"""Shared database fixture for generation self-check and language tests."""
import uuid

import pytest

from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.generation import quiz_graph
from app.models import Chunk, Course, Document, DocumentStatus, User


@pytest.fixture
def document(monkeypatch):
    db = SessionLocal()
    try:
        user = User(email=f"sc-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x",
                    is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Self-check", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.pdf", status=DocumentStatus.embedded, raw_text="text")
        db.add(doc)
        db.flush()
        db.add_all(Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=f"đoạn {i}") for i in range(3))
        db.commit()
        ids = {"user": user.id, "course": course.id, "doc": doc.id}
    finally:
        db.close()

    def fake_retrieve(db, course_id, query, **kwargs):
        return db.query(Chunk).filter(Chunk.course_id == course_id).order_by(Chunk.chunk_index).all()

    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(quiz_graph, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "expand_with_parent_context", lambda db, chunks, **kwargs: chunks)

    yield ids

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
