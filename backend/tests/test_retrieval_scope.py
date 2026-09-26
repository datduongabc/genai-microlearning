"""Retrieval limited to one run of chunks of one document (a chosen chapter)."""

import uuid

import pytest

from app.db import SessionLocal
from app.ingestion.embeddings import embed_texts
from app.models import Chunk, Course, Document, DocumentStatus, User, UserRole
from app.retrieval import search as retrieval
from app.retrieval.search import (
    ChunkScope,
    dense_search,
    expand_with_parent_context,
    invalidate_sparse_cache,
    retrieve,
    sparse_search,
)

TEXTS = [
    "linked list node pointer traversal",
    "linked list insertion deletion in a data structure",
    "doubly linked list previous pointer",
    "sorting algorithm quicksort partition pivot",
    "merge sort divide conquer data structure recursion",
    "sorting stability comparison algorithm complexity",
]


@pytest.fixture
def book():
    db = SessionLocal()
    try:
        user = User(
            email=f"scope-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", role=UserRole.teacher
        )
        db.add(user)
        db.flush()
        course = Course(name="Retrieval scope", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="book.pdf", status=DocumentStatus.embedded)
        db.add(doc)
        db.flush()
        db.add_all(
            Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=text, embedding=vector)
            for i, (text, vector) in enumerate(zip(TEXTS, embed_texts(TEXTS), strict=True))
        )
        db.commit()
        ids = {"user": user.id, "course": course.id, "doc": doc.id}
    finally:
        db.close()

    yield ids

    invalidate_sparse_cache(ids["course"])
    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _indexes(chunks) -> list[int]:
    return sorted(c.chunk_index for c in chunks)


def test_dense_search_without_a_scope_sees_the_whole_course(book):
    db = SessionLocal()
    try:
        assert _indexes(dense_search(db, book["course"], "sorting algorithm", top_k=6)) == [0, 1, 2, 3, 4, 5]
    finally:
        db.close()


def test_dense_search_stays_inside_the_scope(book):
    db = SessionLocal()
    try:
        scope = ChunkScope(book["doc"], 0, 2)
        assert _indexes(dense_search(db, book["course"], "sorting algorithm", top_k=6, scope=scope)) == [0, 1, 2]
    finally:
        db.close()


def test_sparse_search_ranks_only_the_chunks_of_the_scope(book):
    db = SessionLocal()
    try:
        assert _indexes(sparse_search(db, book["course"], "data structure")) == [1, 4]
        first_half = sparse_search(db, book["course"], "data structure", scope=ChunkScope(book["doc"], 0, 2))
        second_half = sparse_search(db, book["course"], "data structure", scope=ChunkScope(book["doc"], 3, 5))
        assert _indexes(first_half) == [1]
        assert _indexes(second_half) == [4]
    finally:
        db.close()


def test_invalidating_a_course_clears_its_scoped_cache_entries_too(book):
    db = SessionLocal()
    try:
        scope = ChunkScope(book["doc"], 0, 5)
        assert sparse_search(db, book["course"], "zebra", scope=scope) == []

        db.add(Chunk(document_id=book["doc"], course_id=book["course"], chunk_index=2, content="zebra crossing"))
        db.commit()
        assert sparse_search(db, book["course"], "zebra", scope=scope) == []  # still the cached index

        invalidate_sparse_cache(book["course"])
        assert any("zebra" in c.content for c in sparse_search(db, book["course"], "zebra", scope=scope))
    finally:
        db.close()


def test_parent_context_does_not_cross_the_scope_boundary(book):
    db = SessionLocal()
    try:
        chunks = db.query(Chunk).filter(Chunk.document_id == book["doc"]).order_by(Chunk.chunk_index).all()
        scope = ChunkScope(book["doc"], 3, 5)

        # without the scope a window of 2 around chunk 3 would reach chunks 1 and 2
        at_start = expand_with_parent_context(db, [chunks[3]], window=2, scope=scope)
        at_end = expand_with_parent_context(db, [chunks[5]], window=2, scope=scope)

        assert at_start[0].content == "\n\n".join(TEXTS[3:6])
        assert at_end[0].content == "\n\n".join(TEXTS[3:6])
    finally:
        db.close()


def test_two_chunks_that_expand_to_the_same_clamped_window_give_one_parent(book):
    db = SessionLocal()
    try:
        chunks = db.query(Chunk).filter(Chunk.document_id == book["doc"]).order_by(Chunk.chunk_index).all()
        scope = ChunkScope(book["doc"], 3, 5)

        expanded = expand_with_parent_context(db, [chunks[3], chunks[5]], window=2, scope=scope)

        assert len(expanded) == 1
    finally:
        db.close()


def test_retrieve_returns_only_chunks_of_the_scope(book, monkeypatch):
    # Reranking is not what is under test and would call the reranker service over HTTP.
    monkeypatch.setattr(
        retrieval, "rerank_scored", lambda query, chunks: [(c, 1.0 - i * 0.01) for i, c in enumerate(chunks)]
    )
    db = SessionLocal()
    try:
        results = retrieve(
            db, book["course"], "sorting algorithm", top_k=None, min_k=2, scope=ChunkScope(book["doc"], 0, 2)
        )

        assert results
        assert all(c.chunk_index <= 2 for c in results)
    finally:
        db.close()
