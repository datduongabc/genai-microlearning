import uuid

from app.db import SessionLocal
from app.models import Chunk, Course, Document, DocumentStatus, User
from app.retrieval import search
from app.retrieval.search import invalidate_sparse_cache, sparse_search


def _seed_course_with_chunks(db, texts: list[str]):
    user = User(email=f"test-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
    db.add(user)
    db.flush()
    course = Course(name="Sparse cache test", owner_id=user.id)
    db.add(course)
    db.flush()
    doc = Document(course_id=course.id, filename="x.pdf", status=DocumentStatus.embedded)
    db.add(doc)
    db.flush()
    for i, text in enumerate(texts):
        db.add(Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=text))
    db.commit()
    return course.id, doc.id


def test_chunks_written_by_another_process_are_seen_without_invalidation():
    # The worker ingests in its own process, so it cannot clear this process's cache.
    db = SessionLocal()
    try:
        course_id, document_id = _seed_course_with_chunks(db, ["binary search", "linked list"])
        assert all("quicksort" not in c.content for c in sparse_search(db, course_id, "quicksort"))
        db.add(Chunk(document_id=document_id, course_id=course_id, chunk_index=99, content="quicksort partition"))
        db.commit()
        assert any("quicksort" in c.content for c in sparse_search(db, course_id, "quicksort"))
    finally:
        db.close()


def test_invalidation_forces_a_rebuild(monkeypatch):
    built = []

    class Counting(search.BM25Okapi):
        def __init__(self, corpus):
            built.append(len(corpus))
            super().__init__(corpus)

    monkeypatch.setattr(search, "BM25Okapi", Counting)
    db = SessionLocal()
    try:
        course_id, _ = _seed_course_with_chunks(db, ["binary search"])
        sparse_search(db, course_id, "binary")
        invalidate_sparse_cache(course_id)
        sparse_search(db, course_id, "binary")
        assert len(built) == 2
    finally:
        db.close()


def test_index_is_built_once_while_the_corpus_is_unchanged(monkeypatch):
    built = []

    class Counting(search.BM25Okapi):
        def __init__(self, corpus):
            built.append(len(corpus))
            super().__init__(corpus)

    monkeypatch.setattr(search, "BM25Okapi", Counting)
    db = SessionLocal()
    try:
        course_id, _ = _seed_course_with_chunks(db, ["binary search", "linked list"])
        sparse_search(db, course_id, "binary")
        sparse_search(db, course_id, "list")
        assert built == [2]
    finally:
        db.close()


def test_cache_keeps_a_bounded_number_of_indexes(monkeypatch):
    monkeypatch.setattr(search, "_BM25_CACHE_MAX_ENTRIES", 2)
    db = SessionLocal()
    try:
        for _ in range(3):
            course_id, _ = _seed_course_with_chunks(db, ["binary search"])
            sparse_search(db, course_id, "binary")
        assert len(search._bm25_cache) <= 2
    finally:
        db.close()


def test_sparse_search_reuses_cache_across_sessions_after_commit_and_close():
    # A request commits and closes its session; the next request, in the same process, hits the cache.
    seed_db = SessionLocal()
    try:
        course_id, _ = _seed_course_with_chunks(
            seed_db,
            [
                "binary search algorithm",
                "linked list structure",
                "hash table buckets",
                "quicksort partition pivot",
            ],
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
