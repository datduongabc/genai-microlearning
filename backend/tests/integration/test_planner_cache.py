import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.rate_limit import limiter
from app.auth.security import create_access_token
from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.generation.module_generation_pipeline import _get_or_cache_planner
from app.generation.planner import Concept, PlannerOutput
from app.main import app
from app.models import Course, Document, DocumentStatus, Job, JobType, User

client = TestClient(app)

PLANNED = [
    Concept(name="Hàm băm", priority=2, summary="Ánh xạ khóa vào chỉ số."),
    Concept(name="Bảng băm", priority=1, summary="Cấu trúc lưu cặp khóa và giá trị."),
    Concept(name="Xung đột", priority=3, summary="Hai khóa cùng một chỉ số."),
]


@pytest.fixture(autouse=True)
def no_limits(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)


@pytest.fixture
def planner(monkeypatch):
    calls = []

    def run(text, output_language="vi", custom_instructions=""):
        calls.append((output_language, custom_instructions))
        return PlannerOutput(concepts=PLANNED)

    monkeypatch.setattr(pipeline, "run_planner", run)
    return calls


@pytest.fixture
def lecturer():
    with SessionLocal() as db:
        user = User(email=f"concepts-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Concepts", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.pdf", status=DocumentStatus.embedded, raw_text="Bảng băm ...")
        db.add(doc)
        db.commit()
        ids = {
            "user": user.id,
            "course": course.id,
            "doc": doc.id,
            "headers": {"Authorization": f"Bearer {create_access_token(str(user.id))}"},
        }
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids["course"]).delete()
        db.query(User).filter(User.id == ids["user"]).delete()
        db.commit()


def _concepts(lecturer, language="vi", headers=None):
    return client.get(
        "/modules/concepts",
        params={"document_id": str(lecturer["doc"]), "output_language": language},
        headers=headers or lecturer["headers"],
    )


def _job_payload(lecturer) -> dict:
    with SessionLocal() as db:
        job = db.query(Job).filter(Job.document_id == lecturer["doc"], Job.job_type == JobType.module_generation).one()
        return job.payload


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


# --- Module concept selection (merged from test_module_concepts.py) ---


def test_the_concepts_come_most_important_first_and_are_asked_for_once(lecturer, planner):
    first = _concepts(lecturer)
    again = _concepts(lecturer)

    assert first.status_code == 200
    assert [c["name"] for c in first.json()] == ["Bảng băm", "Hàm băm", "Xung đột"]
    assert first.json()[0] == {"name": "Bảng băm", "summary": "Cấu trúc lưu cặp khóa và giá trị."}
    assert again.json() == first.json()
    # Without the lecturer's guidance: typing it in the dialog must not ask the planner again.
    assert planner == [("vi", "")]


def test_another_language_asks_the_planner_in_that_language(lecturer, planner):
    _concepts(lecturer, "vi")
    _concepts(lecturer, "en")
    assert planner == [("vi", ""), ("en", "")]


def test_a_planner_that_fails_answers_503_for_the_dialog_to_generate_without_a_pick(lecturer, monkeypatch):
    def down(*_a, **_k):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(pipeline, "run_planner", down)
    response = _concepts(lecturer)
    assert response.status_code == 503
    assert "tự chọn" in response.json()["detail"]


def test_another_lecturers_document_lists_nothing(lecturer, planner):
    with SessionLocal() as db:
        other = User(email=f"other-{uuid.uuid4().hex[:8]}@example.com", name="O", hashed_password="x", is_admin=False)
        db.add(other)
        db.commit()
        other_id = other.id
    try:
        response = _concepts(lecturer, headers={"Authorization": f"Bearer {create_access_token(str(other_id))}"})
        assert response.status_code == 404
        assert planner == []
    finally:
        with SessionLocal() as db:
            db.query(User).filter(User.id == other_id).delete()
            db.commit()


def test_the_picked_concept_goes_to_the_worker_with_the_job(lecturer):
    response = client.post(
        "/modules/generate",
        params={"document_id": str(lecturer["doc"])},
        headers=lecturer["headers"],
        json={"concept": {"name": " Xung đột ", "summary": "Hai khóa cùng một chỉ số."}},
    )

    assert response.status_code == 200
    assert _job_payload(lecturer)["concept"] == {"name": "Xung đột", "summary": "Hai khóa cùng một chỉ số."}


def test_a_generation_without_a_pick_leaves_the_choice_to_the_worker(lecturer):
    response = client.post(
        "/modules/generate", params={"document_id": str(lecturer["doc"])}, headers=lecturer["headers"], json={}
    )

    assert response.status_code == 200
    assert "concept" not in _job_payload(lecturer)


def test_a_concept_without_a_name_is_refused(lecturer):
    response = client.post(
        "/modules/generate",
        params={"document_id": str(lecturer["doc"])},
        headers=lecturer["headers"],
        json={"concept": {"name": "  ", "summary": "x"}},
    )
    assert response.status_code == 422
