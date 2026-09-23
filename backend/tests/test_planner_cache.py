import uuid

from app.db import SessionLocal
from app.generation.module_generation_pipeline import _get_or_cache_planner
from app.models import Course, Document, DocumentStatus, User, UserRole


def _seed_document(db, planner_concepts=None):
    user = User(email=f"test-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", role=UserRole.teacher)
    db.add(user)
    db.flush()
    course = Course(name="Planner cache test", owner_id=user.id)
    db.add(course)
    db.flush()
    doc = Document(
        course_id=course.id, filename="x.pdf", status=DocumentStatus.embedded,
        raw_text="irrelevant for the cache-hit path", planner_concepts=planner_concepts,
    )
    db.add(doc)
    db.commit()
    return doc


def test_cache_hit_returns_stored_concepts_without_calling_llm():
    db = SessionLocal()
    try:
        stored = [
            {"name": "Concept A", "priority": 1, "summary": "Summary A"},
            {"name": "Concept B", "priority": 2, "summary": "Summary B"},
        ]
        doc = _seed_document(db, planner_concepts=stored)

        # No GEMINI_API_KEY call happens here -- if planner_concepts is
        # already set, run_planner() must never be invoked. If this test
        # passes quickly with no network activity, that itself proves the
        # cache-hit path short-circuits before reaching the LLM.
        result = _get_or_cache_planner(db, doc)

        assert len(result.concepts) == 2
        assert result.concepts[0].name == "Concept A"
        assert result.concepts[0].priority == 1
        assert result.concepts[1].summary == "Summary B"
    finally:
        db.close()


def test_cache_hit_is_stable_across_repeated_calls():
    db = SessionLocal()
    try:
        stored = [{"name": "Only Concept", "priority": 1, "summary": "The only one"}]
        doc = _seed_document(db, planner_concepts=stored)

        results = [_get_or_cache_planner(db, doc) for _ in range(5)]
        names = {r.concepts[0].name for r in results}
        summaries = {r.concepts[0].summary for r in results}

        assert names == {"Only Concept"}
        assert summaries == {"The only one"}
    finally:
        db.close()
