"""After the embedding provider changes, the chunks that have no vector are embedded again, in
batches, and the ones that already have one are left alone, so the work can be stopped and started."""

import uuid

import pytest

from app.db import SessionLocal
from app.ingestion.reembed import reembed_missing
from app.models import Chunk, Course, Document, DocumentStatus, User
from app.models.models import EMBEDDING_DIM

KEPT = [1.0] + [0.0] * (EMBEDDING_DIM - 1)


@pytest.fixture
def chunks():
    """Three chunks with a vector and 250 without, in one document."""
    db = SessionLocal()
    try:
        user = User(email=f"reemb-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Re-embed", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.pdf", status=DocumentStatus.embedded, raw_text="t")
        db.add(doc)
        db.flush()
        rows = [
            Chunk(
                document_id=doc.id, course_id=course.id, chunk_index=i, content=f"đoạn số {i} về lập lịch",
                embedding=KEPT if i < 3 else None,
            )
            for i in range(253)
        ]
        db.add_all(rows)
        db.commit()
        ids = {"user": user.id, "course": course.id, "doc": doc.id}
    finally:
        db.close()

    yield ids

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _all(doc_id):
    db = SessionLocal()
    try:
        rows = db.query(Chunk).filter(Chunk.document_id == doc_id).order_by(Chunk.chunk_index).all()
        db.expunge_all()
        return rows
    finally:
        db.close()


def test_every_chunk_without_a_vector_gets_one_of_the_size_of_the_column(chunks):
    db = SessionLocal()
    try:
        done = reembed_missing(db)
    finally:
        db.close()

    rows = _all(chunks["doc"])
    # At least these: it goes through every chunk of the database that has no vector.
    assert done >= 250
    assert all(r.embedding is not None for r in rows)
    assert all(len(r.embedding) == EMBEDDING_DIM for r in rows)


def test_a_chunk_that_already_has_a_vector_keeps_it(chunks):
    db = SessionLocal()
    try:
        reembed_missing(db)
    finally:
        db.close()

    kept = _all(chunks["doc"])[:3]

    assert all(list(r.embedding) == KEPT for r in kept)


def test_running_it_again_has_nothing_left_to_do(chunks):
    db = SessionLocal()
    try:
        reembed_missing(db)
        assert reembed_missing(db) == 0
    finally:
        db.close()


def test_a_small_batch_size_still_reaches_every_chunk(chunks):
    db = SessionLocal()
    try:
        assert reembed_missing(db, batch_size=40) >= 250
    finally:
        db.close()

    assert all(r.embedding is not None for r in _all(chunks["doc"]))


def test_the_background_pass_stops_at_a_refusal_and_frees_its_batch(chunks, monkeypatch):
    """A refused batch must not wait out the limit while it holds its rows: deleting the document would wait as long."""
    from litellm.exceptions import RateLimitError

    from app.ingestion import embeddings, reembed

    def refuse(texts, **request):
        assert request == {"rate_limit_retries": 0}
        raise RateLimitError("limit", llm_provider="openai", model="embed-default")

    monkeypatch.setattr(reembed, "embed_texts", refuse)
    monkeypatch.setattr(embeddings.time, "sleep", lambda _: pytest.fail("the background pass must not wait"))
    db = SessionLocal()
    try:
        assert reembed_missing(db, wait=False) == 0
        assert not db.in_transaction()
    finally:
        db.close()
