import uuid

from app.db import SessionLocal
from app.generation.module_generation_pipeline import _get_or_cache_planner
from app.models import Course, Document, DocumentStatus, User


def _seed_document(db, planner_concepts=None):
    user = User(email=f"test-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
    db.add(user)
    db.flush()
    course = Course(name="Planner cache test", owner_id=user.id)
    db.add(course)
    db.flush()
    doc = Document(
        course_id=course.id, filename="x.pdf", status=DocumentStatus.embedded,
        raw_text="irrelevant for the cache-hit path",
        planner_concepts=[{**c, "output_language": "vi"} for c in planner_concepts] if planner_concepts else None,
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



def test_language_change_refreshes_planner_once(monkeypatch):
    from app.generation import module_generation_pipeline as pipeline
    from app.generation.planner import Concept, PlannerOutput
    calls = []
    def planner(text, output_language="vi", custom_instructions=""):
        calls.append(output_language)
        return PlannerOutput(concepts=[Concept(name="Search" if output_language == "en" else "Tìm kiếm", priority=1, summary="s")])
    monkeypatch.setattr(pipeline, "run_planner", planner)
    with SessionLocal() as db:
        doc = _seed_document(db, [{"name": "Tìm kiếm", "priority": 1, "summary": "s"}])
        assert _get_or_cache_planner(db, doc, output_language="en").concepts[0].name == "Search"
        assert _get_or_cache_planner(db, doc, output_language="en").concepts[0].name == "Search"
        assert calls == ["en"]


def test_switching_language_back_keeps_each_language_cached(monkeypatch):
    from app.generation import module_generation_pipeline as pipeline
    from app.generation.planner import Concept, PlannerOutput
    calls = []
    def planner(text, output_language="vi", custom_instructions=""):
        calls.append((output_language, custom_instructions.strip()))
        return PlannerOutput(concepts=[Concept(name=f"Concept {output_language}", priority=1, summary="s")])
    monkeypatch.setattr(pipeline, "run_planner", planner)
    with SessionLocal() as db:
        doc = _seed_document(db)
        names = [_get_or_cache_planner(db, doc, output_language=lang).concepts[0].name for lang in ("vi", "en", "vi")]
        # A generation with instructions is its own key and evicts neither language.
        _get_or_cache_planner(db, doc, output_language="vi", custom_instructions="  ví dụ  ")
        _get_or_cache_planner(db, doc, output_language="vi", custom_instructions="ví dụ")
        assert _get_or_cache_planner(db, doc, output_language="en").concepts[0].name == "Concept en"
        assert names == ["Concept vi", "Concept en", "Concept vi"]
        assert calls == [("vi", ""), ("en", ""), ("vi", "ví dụ")]


def test_a_list_another_session_cached_while_the_planner_ran_is_kept(monkeypatch):
    from app.generation import module_generation_pipeline as pipeline
    from app.generation.planner import Concept, PlannerOutput
    with SessionLocal() as db:
        db.expire_on_commit = False  # as the worker runs
        doc = _seed_document(db)
        def planner(text, output_language="vi", custom_instructions=""):
            if output_language == "vi":
                with SessionLocal() as other:
                    _get_or_cache_planner(other, other.get(Document, doc.id), output_language="en")
            return PlannerOutput(concepts=[Concept(name=f"Concept {output_language}", priority=1, summary="s")])
        monkeypatch.setattr(pipeline, "run_planner", planner)
        _get_or_cache_planner(db, doc, output_language="vi", custom_instructions="ví dụ")
    with SessionLocal() as db:
        stored = db.get(Document, doc.id).planner_concepts
    assert sorted((c["output_language"], c["custom_instructions"]) for c in stored) == [("en", ""), ("vi", "ví dụ")]


def test_a_title_is_found_in_any_cached_planner_list(monkeypatch):
    from app.generation import module_generation_pipeline as pipeline
    from app.generation.planner import Concept, PlannerOutput
    from app.models import Module
    monkeypatch.setattr(pipeline, "run_planner", lambda text, output_language="vi", custom_instructions="":
                        PlannerOutput(concepts=[Concept(name="Search", priority=1, summary="finding a key")]))
    with SessionLocal() as db:
        doc = _seed_document(db, [{"name": "Tìm kiếm", "priority": 1, "summary": "tìm một khóa"}])
        _get_or_cache_planner(db, doc, output_language="en")
        module = Module(document_id=doc.id, course_id=doc.course_id, title="Tìm kiếm")
        db.add(module)
        db.flush()
        assert pipeline.concept_query_of(module) == "Tìm kiếm: tìm một khóa"
