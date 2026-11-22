"""Admin study loading uses real runtime-role HTTP requests and snapshot packets."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.auth.security import create_access_token
from app.config import settings
from app.db import RequestSessionLocal, SessionLocal
from app.generation.evaluation import ModuleContentInput, ModuleEvaluation
from app.generation.module_generation_pipeline import control_payload
from app.jobs.maintenance import purge_old_records
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
        course = Course(name="Secret course", owner_id=admin.id)
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
    # The rubric is the server's: a client cannot start a study on another one.
    response = client.post("/studies", headers=world["headers"], json={"name": "Study", "rubric_version": "v1"})
    assert response.status_code == 201, response.text
    assert response.json()["rubric_version"] == "v3"
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


@pytest.mark.parametrize("source", ["rag", "control"])
def test_loading_another_owners_source_or_control_returns_not_found(world, source):
    sid = create_study(world)
    with SessionLocal() as db:
        other_course = Course(name="Other owner's course", owner_id=world["teacher"])
        db.add(other_course)
        db.flush()
        other_course_id = other_course.id
        other_doc = Document(course_id=other_course_id, filename="other.txt")
        db.add(other_doc)
        db.flush()
        module = db.get(Module, world[source])
        original_document_id = module.document_id
        module.course_id = other_course_id
        module.document_id = other_doc.id
        if source == "rag":
            db.get(Course, world["course"]).owner_id = world["teacher"]
        db.commit()
    try:
        response = load(world, sid, module_ids=[str(world["rag"])], human_packets=[human_packet()])
        missing = load(world, sid, module_ids=[str(uuid.uuid4())])
        assert response.status_code == missing.status_code == 404
        assert response.json() == missing.json() == {"detail": "Module not found"}
        assert client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"] == []
        with RequestSessionLocal() as db:
            db.execute(text("SELECT set_config('app.current_user_id', :uid, true)"),
                       {"uid": str(world["admin"])})
            assert db.get(Module, world[source]) is None
            assert db.scalars(select(ModuleComponent).where(ModuleComponent.module_id == world[source])).all() == []
            if source == "rag":
                assert db.scalars(select(Chunk).where(Chunk.course_id == world["course"])).all() == []
    finally:
        with SessionLocal() as db:
            module = db.get(Module, world[source])
            module.course_id = world["course"]
            module.document_id = original_document_id
            db.get(Course, world["course"]).owner_id = world["admin"]
            db.flush()
            db.query(Course).filter(Course.id == other_course_id).delete(synchronize_session=False)
            db.commit()


def test_every_endpoint_is_admin_only(world):
    sid = create_study(world)
    load(world, sid, human_packets=[human_packet()])
    for method, path, body in (("GET", "/studies", None), ("GET", f"/studies/{sid}", None),
                               ("POST", "/studies", {"name": "S"}),
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
    listed = client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"]
    assert [{k: v for k, v in item.items() if k != "source"} for item in listed] == before


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
    assert client.post("/studies", headers=world["headers"], json={"name": " "}).status_code == 422
    assert load(world, str(uuid.uuid4()), human_packets=[human_packet()]).status_code == 404


def rating_values():
    score = {"score": 3, "justification": "Acceptable"}
    return ModuleEvaluation(anon_id="M01", **{field: score for field in (
        "accuracy", "completeness", "consistency", "hallucination_score", "slide_quality",
        "quiz_quality", "bloom_fidelity", "query_alignment", "examples_feedback")},
        quiz_flaws=[{"question": 1}, {"question": 2}], overall_notes="Overall").model_dump(exclude={"anon_id"})


def test_loading_freezes_after_a_human_rating_or_leaving_draft(world):
    sid = create_study(world)
    load(world, sid, human_packets=[human_packet()])
    with SessionLocal() as db:
        item = db.scalar(select(StudyItem).where(StudyItem.study_id == uuid.UUID(sid)))
        db.add(Rating(study_item_id=item.id, rater_kind="human", user_id=world["admin"], **rating_values()))
        db.commit()
    assert load(world, sid, human_packets=[human_packet()]).status_code == 409
    other = create_study(world)
    with SessionLocal() as db:
        db.get(Study, uuid.UUID(other)).status = "active"
        db.commit()
    assert load(world, other, human_packets=[human_packet()]).status_code == 409


def test_rls_denies_study_data_to_non_admin_and_other_owners(world):
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
        assert db.get(Module, world["rag"]) is not None


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
        db.add(Rating(study_item_id=item.id, rater_kind="llm_judge", judge_family="openai", **rating_values()))
        db.commit()
        db.add(Rating(study_item_id=item.id, rater_kind="llm_judge", judge_family="openai", **rating_values()))
        with pytest.raises(IntegrityError):
            db.commit()



def test_loading_pair_survives_job_retention(world):
    with SessionLocal() as db:
        for key in ("rag", "control"):
            module = db.get(Module, world[key])
            job = db.scalar(select(Job).where(Job.module_id == module.id))
            module.generation_payload = job.payload
            if key == "control":
                module.paired_module_id = world["rag"]
            job.created_at = datetime.now(UTC) - timedelta(days=settings.done_job_retention_days + 1)
        db.commit()
    purge_old_records()
    with SessionLocal() as db:
        assert db.scalars(select(Job).where(Job.module_id.in_([world["rag"], world["control"]]))).all() == []
    sid = create_study(world)
    assert load(world, sid, module_ids=[str(world["rag"])]).status_code == 201
    assert load(world, create_study(world), module_ids=[str(world["control"])]).status_code == 422


@pytest.mark.parametrize("kind", ["modules", "documents", "courses"])
def test_study_reference_blocks_deletion_before_file_removal(world, kind, monkeypatch):
    from pathlib import Path

    sid = create_study(world)
    assert load(world, sid, module_ids=[str(world["rag"])]).status_code == 201
    with SessionLocal() as db:
        document_id = db.get(Module, world["rag"]).document_id
    upload_dir = Path(".pytest_cache/study_uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload = upload_dir / f"{document_id}.txt"
    upload.write_text("retained upload")
    monkeypatch.setattr(settings, "upload_storage_dir", str(upload_dir))
    target = {"modules": world["rag"], "documents": document_id, "courses": world["course"]}[kind]
    try:
        response = client.delete(f"/{kind}/{target}", headers=world["headers"])
        assert response.status_code == 409, response.text
        assert upload.read_text() == "retained upload"
        with SessionLocal() as db:
            assert db.get(Module, world["rag"]) is not None
            assert db.get(Module, world["control"]) is not None
            assert db.get(Document, document_id) is not None
            assert db.get(Course, world["course"]) is not None
    finally:
        upload.unlink(missing_ok=True)



def test_lecturer_deletion_detects_reference_without_study_read_access(world):
    sid = create_study(world)
    assert load(world, sid, module_ids=[str(world["rag"])]).status_code == 201
    with SessionLocal() as db:
        db.get(Course, world["course"]).owner_id = world["teacher"]
        db.commit()
    response = client.delete(f"/modules/{world['rag']}", headers=world["teacher_headers"])
    assert response.status_code == 409, response.text
    with SessionLocal() as db:
        assert db.get(Module, world["rag"]) is not None
        db.get(Course, world["course"]).owner_id = world["admin"]
        db.commit()



@pytest.mark.parametrize("field", ["quiz", "practice"])
@pytest.mark.parametrize("choices,index", [([], 0), (["X", "Y"], -1), (["X", "Y"], 2)])
def test_invalid_answer_rejects_entire_load_without_reblinding(world, field, choices, index):
    import copy

    sid = create_study(world)
    assert load(world, sid, human_packets=[human_packet()]).status_code == 201
    before = client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"]
    invalid = copy.deepcopy(human_packet())
    question = invalid["quiz"][0] if field == "quiz" else invalid["practice"]
    question["choices"] = choices
    question["correct_index"] = index
    response = load(world, sid, module_ids=[str(world["rag"])],
                    human_packets=[human_packet(), invalid])
    assert response.status_code == 422, response.text
    assert client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"] == before



def test_legacy_missing_defaults_import_and_export_reuse_after_backfill_and_purge(world, monkeypatch, tmp_path):
    from app.generation.module_generation_pipeline import (
        matching_control,
        normalize_generation_payload,
    )
    from scripts.export_study_pair import main as export_pair

    with SessionLocal() as db:
        for key in ("rag", "control"):
            job = db.scalar(select(Job).where(Job.module_id == world[key]))
            job.payload = {name: value for name, value in job.payload.items()
                           if name not in ("self_check", "output_language", "custom_instructions")}
        db.commit()
    assert load(world, create_study(world), module_ids=[str(world["rag"])]).status_code == 201
    with SessionLocal() as db:
        for key in ("rag", "control"):
            job = db.scalar(select(Job).where(Job.module_id == world[key]))
            module = db.get(Module, world[key])
            module.generation_payload = normalize_generation_payload(job.payload)
            if key == "control":
                module.paired_module_id = world["rag"]
        db.query(Job).filter(Job.module_id.in_([world["rag"], world["control"]])).delete(synchronize_session=False)
        db.commit()
        expected = control_payload(world["rag"], "test-model", True)
        assert matching_control(db, world["rag"], expected).id == world["control"]
        for payload in (
            control_payload(world["rag"], "other-model", True),
            control_payload(world["rag"], "test-model", False),
            control_payload(world["rag"], "test-model", True, "en"),
            control_payload(world["rag"], "test-model", True, "vi", "Different instructions"),
        ):
            assert matching_control(db, world["rag"], payload) is None
    assert load(world, create_study(world), module_ids=[str(world["rag"])]).status_code == 201

    def unexpected_generation(*args, **kwargs):
        raise AssertionError("The backfilled control must be reused")

    monkeypatch.setattr("app.generation.study_packets.run_module_generation", unexpected_generation)
    for _ in range(2):
        export_pair([str(world["rag"]), "--out", str(tmp_path)])
    with SessionLocal() as db:
        assert db.query(Module).filter(Module.paired_module_id == world["rag"]).count() == 1


def test_edited_ai_pair_import_and_cli_export_use_original(world, tmp_path):
    from scripts.export_study_pair import main as export_pair

    originals = {}
    with SessionLocal() as db:
        for key in ("rag", "control"):
            module = db.get(Module, world[key])
            component = next(c for c in module.components if c.type == ComponentType.content)
            originals[key] = component.content["markdown"]
            component.content = {"markdown": "Teacher replacement", "ai_original": component.content}
        db.commit()
    sid = create_study(world)
    response = load(world, sid, module_ids=[str(world["rag"])])
    assert response.status_code == 201, response.text
    items = client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"]
    for item in items:
        key = "rag" if item["group"] == "rag_llm" else "control"
        assert item["packet"]["content_markdown"] == originals[key]
    export_pair([str(world["rag"]), "--out", str(tmp_path)])
    import json
    for group, key in [("rag_llm", "rag"), ("llm_no_rag", "control")]:
        exported = json.loads((tmp_path / f"{world['rag']}-{group}.json").read_text())
        assert exported["packet"]["content_markdown"] == originals[key]


def _imported(pair_id, group, objective="Explain X"):
    return {"pair_id": str(pair_id), "group": group, "generator_model": "gemini-fast",
            "packet": {**human_packet(), "learning_objective": objective}}


def test_loads_exported_pairs_without_their_modules_and_pairs_them(world):
    sid = create_study(world)
    pair = uuid.uuid4()

    response = load(world, sid, imported_packets=[_imported(pair, "rag_llm"), _imported(pair, "llm_no_rag")])

    assert response.status_code == 201, response.text
    items = response.json()
    assert sorted(i["module_id"] is None for i in items) == [True, True]
    assert {i["pair_id"] for i in items} == {str(pair)}
    # Reblinded on load: the exported anon_id is replaced by the study's own code.
    assert sorted(i["packet"]["anon_id"] for i in items) == sorted(i["anon_code"] for i in items)
    # Loading the same pair again is refused, so a rerun of the import adds nothing.
    again = load(world, sid, imported_packets=[_imported(pair, "rag_llm"), _imported(pair, "llm_no_rag")])
    assert again.status_code == 409


def test_refuses_an_imported_pair_that_is_not_one_rag_and_one_no_rag(world):
    sid = create_study(world)
    pair = uuid.uuid4()

    alone = load(world, sid, imported_packets=[_imported(pair, "rag_llm")])
    twice = load(world, sid, imported_packets=[_imported(pair, "rag_llm"), _imported(pair, "rag_llm")])

    assert alone.status_code == 422 and twice.status_code == 422


def test_candidates_list_the_admins_rag_modules_and_where_they_stand(world):
    sid = create_study(world)
    rows = client.get(f"/studies/{sid}/candidates", headers=world["headers"]).json()
    # The control is not offered; the RAG module's control is already made.
    assert [(r["id"], r["state"]) for r in rows] == [(str(world["rag"]), "ready")]

    added = client.post(f"/studies/{sid}/pairs", headers=world["headers"], json={"module_id": str(world["rag"])})

    assert added.status_code == 201, added.text
    assert sorted(i["group"] for i in added.json()) == ["llm_no_rag", "rag_llm"]
    assert client.get(f"/studies/{sid}/candidates", headers=world["headers"]).json()[0]["state"] == "in_study"
    # Both members of the pair are listed for the admin under the RAG module's title.
    with SessionLocal() as db:
        title = db.get(Module, world["rag"]).title
    assert {i["source"]["title"] for i in client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"]} == {title}
    assert client.post(f"/studies/{sid}/pairs", headers=world["headers"],
                       json={"module_id": str(world["rag"])}).status_code == 409
    # Removing one packet of a pair removes both, and the module can be added again.
    first = added.json()[0]["id"]
    assert client.delete(f"/studies/{sid}/items/{first}", headers=world["headers"]).json() == []
    assert client.post(f"/studies/{sid}/pairs", headers=world["headers"],
                       json={"module_id": str(world["rag"])}).status_code == 201


def test_adding_a_module_without_its_control_asks_for_one_and_says_it_is_generating(world, monkeypatch):
    with SessionLocal() as db:
        db.query(Job).filter(Job.module_id == world["control"]).delete()
        db.commit()
    with SessionLocal() as db:
        doc = db.get(Module, world["rag"]).document_id
        db.get(Document, doc).raw_text = "X is a concept"
        db.commit()
    monkeypatch.setattr("app.api.modules._usable_alias", lambda alias: alias)
    sid = create_study(world)

    first = client.post(f"/studies/{sid}/pairs", headers=world["headers"], json={"module_id": str(world["rag"])})
    again = client.post(f"/studies/{sid}/pairs", headers=world["headers"], json={"module_id": str(world["rag"])})

    assert first.status_code == 202 and first.json() == {"state": "generating"}
    assert again.status_code == 202
    with SessionLocal() as db:
        jobs = db.query(Job).filter(Job.payload["paired_module_id"].astext == str(world["rag"]),
                                    Job.status == JobStatus.queued).all()
        assert len(jobs) == 1  # asking twice queues one generation
        db.query(Job).filter(Job.id == jobs[0].id).delete()
        db.commit()


def test_a_lecturer_cannot_add_pairs(world):
    sid = create_study(world)
    assert client.post(f"/studies/{sid}/pairs", headers=world["teacher_headers"],
                       json={"module_id": str(world["rag"])}).status_code == 404
    assert client.get(f"/studies/{sid}/candidates", headers=world["teacher_headers"]).status_code == 404


def test_a_closed_study_is_listed_to_nobody_and_keeps_its_items(world):
    sid = create_study(world)
    load(world, sid, module_ids=[str(world["rag"])]).raise_for_status()
    with SessionLocal() as db:
        from app.models import User
        study = db.get(Study, sid)
        email = db.get(User, world["teacher"]).email.lower()
        study.rater_emails, study.status = [email], "closed"
        db.commit()
    assert all(s["id"] != sid for s in client.get("/studies", headers=world["headers"]).json())
    assert all(s["id"] != sid for s in client.get("/studies/mine", headers=world["teacher_headers"]).json())
    # Still there for the export.
    assert len(client.get(f"/studies/{sid}", headers=world["headers"]).json()["items"]) == 2


def test_candidates_say_which_control_is_being_made_failed_or_never_asked_for(world):
    with SessionLocal() as db:
        doc = db.scalar(select(Document).where(Document.course_id == world["course"]))
        made = {}
        for label, last in (("Being made", JobStatus.queued), ("Failed", JobStatus.failed), ("Never", None)):
            module = Module(document_id=doc.id, course_id=world["course"], title=label, bloom_level="understand",
                            learning_objective="Explain X", sub_objectives=["Define X", "Describe X"],
                            generation_payload={"model_alias": "test-model"})
            db.add(module)
            db.flush()
            if last is not None:
                db.add(Job(job_type=JobType.module_generation, document_id=doc.id, step="plan", status=last,
                           payload=control_payload(module.id, "test-model", True)))
            made[str(module.id)] = label
        db.commit()
    sid = create_study(world)

    rows = client.get(f"/studies/{sid}/candidates", headers=world["headers"]).json()

    assert {made.get(r["id"], "RAG"): r["state"] for r in rows} == {
        "RAG": "ready", "Being made": "generating", "Failed": "failed", "Never": "none"}
