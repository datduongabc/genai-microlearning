"""Admin study loading uses real runtime-role HTTP requests and snapshot packets."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.auth.security import create_access_token
from app.db import RequestSessionLocal, SessionLocal
from app.generation.evaluation import ModuleContentInput, ModuleEvaluation
from app.generation.module_generation_pipeline import control_payload
from app.main import app
from app.models import (
    Chunk,
    ComponentType,
    Course,
    Document,
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
    Question,
    Rating,
    Study,
    StudyItem,
    User,
)

client = TestClient(app)


def human_packet():
    question = {"stem": "What is X?", "choices": ["X", "Y"], "correct_index": 0,
                "explanation": "X is correct"}
    return {"anon_id": "human-author", "topic": "Topic", "bloom_level": "understand",
            "learning_objective": "Explain X", "sub_objectives": ["Define X", "Describe X"],
            "content_markdown": "X is a concept", "slides": [{"title": "X", "bullets": ["Concept"]}],
            "examples": [{"context": "Example X", "analysis": "Uses X"}],
            "quiz": [question], "practice": question, "source_chunks": ["X is a concept"]}


@pytest.fixture
def world():
    with SessionLocal() as db:
        admin = User(email=f"study-admin-{uuid.uuid4()}@example.com", name="Admin", hashed_password="x", is_admin=True)
        teacher = User(email=f"study-teacher-{uuid.uuid4()}@example.com", name="Teacher", hashed_password="x")
        db.add_all([admin, teacher])
        db.flush()
        course = Course(name="Secret course", owner_id=teacher.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="secret.txt")
        db.add(doc)
        db.flush()
        modules = []
        for label in ("RAG topic", "Control topic"):
            module = Module(document_id=doc.id, course_id=course.id, title=label, bloom_level="understand",
                            learning_objective="Explain X", sub_objectives=["Define X", "Describe X"])
            db.add(module)
            db.flush()
            modules.append(module)
            for kind in (ComponentType.content, ComponentType.quiz, ComponentType.practice):
                component = ModuleComponent(module_id=module.id, type=kind,
                                            content={"markdown": "X is a concept"} if kind == ComponentType.content else {})
                db.add(component)
                db.flush()
                if kind != ComponentType.content:
                    db.add(Question(component_id=component.id, stem="What is X?", choices={"options": ["X", "Y"]},
                                    answer_key="0", explanation="X is correct"))
        db.add(Chunk(document_id=doc.id, course_id=course.id, chunk_index=0, content="X is a concept"))
        db.add(Job(job_type=JobType.module_generation, module_id=modules[0].id, document_id=doc.id,
                   step="done", status=JobStatus.done, payload={"model_alias": "test-model", "self_check": True}))
        db.add(Job(job_type=JobType.module_generation, module_id=modules[1].id, document_id=doc.id,
                   step="done", status=JobStatus.done, payload=control_payload(modules[0].id, "test-model", True)))
        db.commit()
        ids = {"admin": admin.id, "teacher": teacher.id, "course": course.id,
               "rag": modules[0].id, "control": modules[1].id}
    ids["headers"] = {"Authorization": "Bearer " + create_access_token(ids["admin"])}
    ids["teacher_headers"] = {"Authorization": "Bearer " + create_access_token(ids["teacher"])}
    ids["studies"] = []
    yield ids
    with SessionLocal() as db:
        db.query(Study).filter(Study.id.in_(ids["studies"])).delete(synchronize_session=False)
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids["admin"], ids["teacher"]])).delete(synchronize_session=False)
        db.commit()


def create_study(world):
    response = client.post("/studies", headers=world["headers"], json={"name": "Study", "rubric_version": "v1"})
    assert response.status_code == 201, response.text
    world["studies"].append(uuid.UUID(response.json()["id"]))
    return response.json()["id"]


def load(world, sid, **body):
    return client.post(f"/studies/{sid}/items", headers=world["headers"], json=body)


def test_create_list_get_and_load_three_groups_without_exposing_source_metadata(world):
    sid = create_study(world)
    response = load(world, sid, module_ids=[str(world["rag"])], human_packets=[human_packet()])
    assert response.status_code == 201, response.text
    items = response.json()
    assert {i["group"] for i in items} == {"human", "rag_llm", "llm_no_rag"}
    assert {i["anon_code"] for i in items} == {"M01", "M02", "M03"}
    for item in items:
        packet = ModuleContentInput.model_validate(item["packet"])
        assert packet.anon_id == item["anon_code"]
        assert packet.source_chunks == ["X is a concept"]
        assert item["module_id"] == (str(world["rag"]) if item["group"] == "rag_llm" else None)
        assert "secret" not in str(item["packet"]).lower()
        assert "test-model" not in str(item["packet"])
    assert any(s["id"] == sid for s in client.get("/studies", headers=world["headers"]).json())
    detail = client.get(f"/studies/{sid}", headers=world["headers"]).json()
    assert detail["status"] == "draft" and len(detail["items"]) == 3
    with SessionLocal() as db:
        assert db.scalar(select(Rating).join(StudyItem).where(StudyItem.study_id == uuid.UUID(sid))) is None


def test_every_endpoint_is_admin_only(world):
    sid = create_study(world)
    load(world, sid, human_packets=[human_packet()])
    for method, path, body in (("GET", "/studies", None), ("GET", f"/studies/{sid}", None),
                               ("POST", "/studies", {"name": "S", "rubric_version": "v1"}),
                               ("POST", f"/studies/{sid}/items", {"human_packets": [human_packet()]})):
        for headers, status in (({}, 401), (world["teacher_headers"], 404)):
            response = client.request(method, path, headers=headers, json=body)
            assert response.status_code == status
            assert "group" not in response.json() and "items" not in response.json()


def test_incremental_loading_reassigns_unique_codes_and_packet_ids(world, monkeypatch):
    import random

    monkeypatch.setattr(random.SystemRandom, "shuffle", lambda self, items: items.reverse())
    sid = create_study(world)
    assert load(world, sid, module_ids=[str(world["rag"])]).status_code == 201
    response = load(world, sid, human_packets=[human_packet()])
    assert response.status_code == 201, response.text
    assert {i["anon_code"] for i in response.json()} == {"M01", "M02", "M03"}
    assert all(i["packet"]["anon_id"] == i["anon_code"] for i in response.json())


def test_concurrent_loads_serialize_and_cannot_duplicate_a_rag_source(world):
    from concurrent.futures import ThreadPoolExecutor

    sid = create_study(world)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: load(world, sid, module_ids=[str(world["rag"])]), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    items = client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"]
    assert len(items) == 2 and {item["anon_code"] for item in items} == {"M01", "M02"}


def test_packet_is_a_snapshot_of_the_source_module(world):
    sid = create_study(world)
    before = load(world, sid, module_ids=[str(world["rag"])]).json()
    with SessionLocal() as db:
        content = db.scalar(select(ModuleComponent).where(ModuleComponent.module_id == world["rag"],
                                                         ModuleComponent.type == ComponentType.content))
        content.content = {"markdown": "Changed source"}
        db.commit()
    assert client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"] == before


def test_loading_is_atomic_on_duplicate_or_missing_sources(world):
    sid = create_study(world)
    response = load(world, sid, module_ids=[str(world["rag"]), str(uuid.uuid4())], human_packets=[human_packet()])
    assert response.status_code == 404
    assert client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"] == []
    assert load(world, sid, module_ids=[str(world["rag"])]).status_code == 201
    assert load(world, sid, module_ids=[str(world["rag"])], human_packets=[human_packet()]).status_code == 409
    assert len(client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"]) == 2


def test_control_must_exist_and_match_rag_generation_conditions(world):
    sid = create_study(world)
    assert load(world, sid, module_ids=[str(world["control"])]).status_code == 422
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.module_id == world["control"]))
        job.payload = control_payload(world["rag"], "different-model", True)
        db.commit()
    assert load(world, sid, module_ids=[str(world["rag"])]).status_code == 409
    assert client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"] == []


def test_invalid_manual_packets_and_empty_load_are_rejected(world):
    sid = create_study(world)
    packet = human_packet()
    packet["quiz"] = []
    assert load(world, sid, human_packets=[packet]).status_code == 422
    assert load(world, sid).status_code == 422
    assert client.post("/studies", headers=world["headers"], json={"name": " ", "rubric_version": "v1"}).status_code == 422
    assert load(world, str(uuid.uuid4()), human_packets=[human_packet()]).status_code == 404


def rating_values():
    score = {"score": 3, "justification": "Acceptable"}
    return ModuleEvaluation(anon_id="M01", **{field: score for field in (
        "accuracy", "completeness", "consistency", "hallucination_score", "slide_quality",
        "quiz_quality", "bloom_fidelity", "query_alignment")}, overall_notes="Overall").model_dump(exclude={"anon_id"})


def test_loading_freezes_after_a_rating_or_leaving_draft(world):
    sid = create_study(world)
    load(world, sid, human_packets=[human_packet()])
    with SessionLocal() as db:
        item = db.scalar(select(StudyItem).where(StudyItem.study_id == uuid.UUID(sid)))
        db.add(Rating(study_item_id=item.id, rater_kind="llm_judge", **rating_values()))
        db.commit()
    assert load(world, sid, human_packets=[human_packet()]).status_code == 409
    other = create_study(world)
    with SessionLocal() as db:
        db.get(Study, uuid.UUID(other)).status = "active"
        db.commit()
    assert load(world, other, human_packets=[human_packet()]).status_code == 409


def test_rls_denies_study_data_to_non_admin_and_source_reads_outside_import(world):
    sid = create_study(world)
    load(world, sid, module_ids=[str(world["rag"])])
    with RequestSessionLocal() as db:
        db.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": str(world["teacher"])})
        assert db.scalars(select(Study)).all() == []
        assert db.scalars(select(StudyItem)).all() == []
        assert db.scalars(select(Rating)).all() == []
        db.add(Study(name="Forbidden", rubric_version="v1"))
        with pytest.raises(ProgrammingError):
            db.commit()
    with RequestSessionLocal() as db:
        db.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": str(world["admin"])})
        assert db.get(Study, uuid.UUID(sid)) is not None
        assert db.get(Module, world["rag"]) is None


def test_database_enforces_source_and_rater_identity(world):
    sid = create_study(world)
    with SessionLocal() as db:
        db.add(StudyItem(study_id=uuid.UUID(sid), group="human", module_id=world["rag"], anon_code="M01", packet=human_packet()))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        item = StudyItem(study_id=uuid.UUID(sid), group="human", anon_code="M01", packet=human_packet())
        db.add(item)
        db.commit()
        db.add(Rating(study_item_id=item.id, rater_kind="human", **rating_values()))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(Rating(study_item_id=item.id, rater_kind="llm_judge", **rating_values()))
        db.commit()
        db.add(Rating(study_item_id=item.id, rater_kind="llm_judge", **rating_values()))
        with pytest.raises(IntegrityError):
            db.commit()
