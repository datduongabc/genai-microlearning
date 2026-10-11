import uuid

from app.db import SessionLocal
from app.models import Chunk, Course, Document, DocumentStatus, User
from app.retrieval.search import expand_with_parent_context


def _seed_document_with_chunks(db, texts: list[str]):
    user = User(email=f"test-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
    db.add(user)
    db.flush()
    course = Course(name="Parent context test", owner_id=user.id)
    db.add(course)
    db.flush()
    doc = Document(course_id=course.id, filename="x.pdf", status=DocumentStatus.embedded)
    db.add(doc)
    db.flush()

    chunks = []
    for i, text in enumerate(texts):
        c = Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=text)
        db.add(c)
        chunks.append(c)
    db.commit()
    return chunks


def test_expand_with_parent_context_returns_neighboring_physical_chunks():
    db = SessionLocal()
    try:
        chunks = _seed_document_with_chunks(db, ["A", "B", "C", "D", "E"])
        # matched child = chunk_index 2 ("C"), window=1 -> should pull in B, C, D only
        expanded = expand_with_parent_context(db, [chunks[2]], window=1)
        assert [c.id for c in expanded] == [c.id for c in chunks[1:4]]
        assert [c.content for c in expanded] == ["B", "C", "D"]
    finally:
        db.close()


def test_expand_with_parent_context_clamps_at_document_start():
    db = SessionLocal()
    try:
        chunks = _seed_document_with_chunks(db, ["A", "B", "C"])
        # matched child = chunk_index 0 ("A"), window=2 -> can't go below 0
        expanded = expand_with_parent_context(db, [chunks[0]], window=2)
        assert [c.content for c in expanded] == ["A", "B", "C"]
    finally:
        db.close()


def test_expand_with_parent_context_deduplicates_overlapping_windows():
    db = SessionLocal()
    try:
        chunks = _seed_document_with_chunks(db, ["A", "B", "C", "D", "E"])
        expanded = expand_with_parent_context(db, [chunks[2], chunks[1]], window=1)
        assert [c.id for c in expanded] == [c.id for c in chunks[:4]]
        assert [c.content for c in expanded] == ["A", "B", "C", "D"]
    finally:
        db.close()
