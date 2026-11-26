"""Retrieval limited to one run of chunks of one document (a chosen chapter)."""

import uuid

import pytest

from app import timing
from app.db import SessionLocal
from app.ingestion.embeddings import embed_texts
from app.models import Chunk, Course, Document, DocumentStatus, User
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
            email=f"scope-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False
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


def test_a_scoped_index_sees_new_chunks_without_invalidation(book):
    db = SessionLocal()
    try:
        scope = ChunkScope(book["doc"], 0, 5)
        assert sparse_search(db, book["course"], "zebra", scope=scope) == []

        db.add(Chunk(document_id=book["doc"], course_id=book["course"], chunk_index=2, content="zebra crossing"))
        db.commit()
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

        assert [c.content for c in at_start] == TEXTS[3:6]
        assert [c.content for c in at_end] == TEXTS[3:6]
    finally:
        db.close()


def test_two_chunks_that_expand_to_the_same_clamped_window_return_each_physical_chunk_once(book):
    db = SessionLocal()
    try:
        chunks = db.query(Chunk).filter(Chunk.document_id == book["doc"]).order_by(Chunk.chunk_index).all()
        scope = ChunkScope(book["doc"], 3, 5)

        expanded = expand_with_parent_context(db, [chunks[3], chunks[5]], window=2, scope=scope)

        assert [c.id for c in expanded] == [c.id for c in chunks[3:6]]
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


# --- Parent context expansion & sparse cache (merged from test_parent_context.py and test_sparse_cache.py) ---


@pytest.fixture
def seeded_cleanup():
    created: list[tuple[uuid.UUID, uuid.UUID]] = []
    yield created
    for course_id, _ in created:
        invalidate_sparse_cache(course_id)
    if created:
        with SessionLocal() as db:
            db.query(Course).filter(Course.id.in_([c for c, _ in created])).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_([u for _, u in created])).delete(synchronize_session=False)
            db.commit()


def _seed_course_with_chunks(
    db, texts: list[str], created: list[tuple[uuid.UUID, uuid.UUID]], *, course_name: str = "Retrieval test"
) -> tuple[uuid.UUID, uuid.UUID, list[Chunk]]:
    user = User(email=f"test-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
    db.add(user)
    db.flush()
    course = Course(name=course_name, owner_id=user.id)
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
    created.append((course.id, user.id))
    return course.id, doc.id, chunks


def test_expand_with_parent_context_returns_neighboring_physical_chunks(seeded_cleanup):
    db = SessionLocal()
    try:
        _, _, chunks = _seed_course_with_chunks(db, ["A", "B", "C", "D", "E"], seeded_cleanup, course_name="Parent context test")
        # matched child = chunk_index 2 ("C"), window=1 -> should pull in B, C, D only
        expanded = expand_with_parent_context(db, [chunks[2]], window=1)
        assert [c.id for c in expanded] == [c.id for c in chunks[1:4]]
        assert [c.content for c in expanded] == ["B", "C", "D"]
    finally:
        db.close()


def test_expand_with_parent_context_clamps_at_document_start(seeded_cleanup):
    db = SessionLocal()
    try:
        _, _, chunks = _seed_course_with_chunks(db, ["A", "B", "C"], seeded_cleanup, course_name="Parent context test")
        # matched child = chunk_index 0 ("A"), window=2 -> can't go below 0
        expanded = expand_with_parent_context(db, [chunks[0]], window=2)
        assert [c.content for c in expanded] == ["A", "B", "C"]
    finally:
        db.close()


def test_expand_with_parent_context_deduplicates_overlapping_windows(seeded_cleanup):
    db = SessionLocal()
    try:
        _, _, chunks = _seed_course_with_chunks(db, ["A", "B", "C", "D", "E"], seeded_cleanup, course_name="Parent context test")
        expanded = expand_with_parent_context(db, [chunks[2], chunks[1]], window=1)
        assert [c.id for c in expanded] == [c.id for c in chunks[:4]]
        assert [c.content for c in expanded] == ["A", "B", "C", "D"]
    finally:
        db.close()


def test_chunks_written_by_another_process_are_seen_without_invalidation(seeded_cleanup):
    # The worker ingests in its own process, so it cannot clear this process's cache.
    db = SessionLocal()
    try:
        course_id, document_id, _ = _seed_course_with_chunks(db, ["binary search", "linked list"], seeded_cleanup)
        assert all("quicksort" not in c.content for c in sparse_search(db, course_id, "quicksort"))
        db.add(Chunk(document_id=document_id, course_id=course_id, chunk_index=99, content="quicksort partition"))
        db.commit()
        assert any("quicksort" in c.content for c in sparse_search(db, course_id, "quicksort"))
    finally:
        db.close()


def test_invalidation_forces_a_rebuild(monkeypatch, seeded_cleanup):
    built = []

    class Counting(retrieval.BM25Okapi):
        def __init__(self, corpus):
            built.append(len(corpus))
            super().__init__(corpus)

    monkeypatch.setattr(retrieval, "BM25Okapi", Counting)
    db = SessionLocal()
    try:
        course_id, _, _ = _seed_course_with_chunks(db, ["binary search"], seeded_cleanup)
        sparse_search(db, course_id, "binary")
        invalidate_sparse_cache(course_id)
        sparse_search(db, course_id, "binary")
        assert len(built) == 2
    finally:
        db.close()


def test_index_is_built_once_while_the_corpus_is_unchanged(monkeypatch, seeded_cleanup):
    built = []

    class Counting(retrieval.BM25Okapi):
        def __init__(self, corpus):
            built.append(len(corpus))
            super().__init__(corpus)

    monkeypatch.setattr(retrieval, "BM25Okapi", Counting)
    db = SessionLocal()
    try:
        course_id, _, _ = _seed_course_with_chunks(db, ["binary search", "linked list"], seeded_cleanup)
        sparse_search(db, course_id, "binary")
        sparse_search(db, course_id, "list")
        assert built == [2]
    finally:
        db.close()


def test_cache_keeps_a_bounded_number_of_indexes(monkeypatch, seeded_cleanup):
    monkeypatch.setattr(retrieval, "_BM25_CACHE_MAX_ENTRIES", 2)
    db = SessionLocal()
    try:
        for _ in range(3):
            course_id, _, _ = _seed_course_with_chunks(db, ["binary search"], seeded_cleanup)
            sparse_search(db, course_id, "binary")
        assert len(retrieval._bm25_cache) <= 2
    finally:
        db.close()


def test_sparse_search_reuses_cache_across_sessions_after_commit_and_close(seeded_cleanup):
    # A request commits and closes its session; the next request, in the same process, hits the cache.
    seed_db = SessionLocal()
    try:
        course_id, _, _ = _seed_course_with_chunks(
            seed_db,
            [
                "binary search algorithm",
                "linked list structure",
                "hash table buckets",
                "quicksort partition pivot",
            ],
            seeded_cleanup,
        )
    finally:
        seed_db.close()

    first = SessionLocal()
    try:
        results = sparse_search(first, course_id, "quicksort pivot")
        assert [c.content for c in results] == ["quicksort partition pivot"]
        first.commit()  # expires every object the session loaded, as the chat endpoint's commit does
    finally:
        first.close()

    second = SessionLocal()
    try:
        results = sparse_search(second, course_id, "hash buckets")
        assert [c.content for c in results] == ["hash table buckets"]
        assert all(c in second for c in results)
        assert results[0].id is not None
    finally:
        second.close()


def test_search_is_timed_for_the_generation_profile(seeded_cleanup):
    db = SessionLocal()
    try:
        course_id, _, _ = _seed_course_with_chunks(db, ["binary search"], seeded_cleanup)
        with timing.capture_timings() as spans:
            sparse_search(db, course_id, "binary")
        assert [s["kind"] for s in spans] == ["bm25"]
    finally:
        db.close()
