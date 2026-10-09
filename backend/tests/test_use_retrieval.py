"""One pipeline for both arms of the RAG vs no-RAG study: `run_module_generation(use_retrieval=False)`
takes the learning objective from the paired RAG module, never searches, and sends every step the
same prompt as the RAG arm minus the SOURCE CHUNK block, with the questions reading the Content in
its place. The no-RAG job is queued through `POST /modules/generate`.

Every model call goes to a fake that records it, and search is replaced too, so no quota is used."""

import importlib
import pkgutil
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app import generation
from app.auth.security import create_access_token
from app.db import SessionLocal
from app.generation import content, example, quiz_graph
from app.generation import module_generation_pipeline as pipeline
from app.generation.common import IGNORE_EMBEDDED_INSTRUCTIONS
from app.generation.content import ContentOutput, LessonText
from app.generation.example import Example, ExampleOutput
from app.generation.objective import LearningObjective
from app.generation.planner import Concept, PlannerOutput
from app.generation.quiz_graph import QuizQuestion
from app.generation.relevance import RelevanceCheck
from app.generation.slides import Slide, SlidesOutput
from app.llm import catalog
from app.main import app
from app.models import (
    Chunk,
    Course,
    Document,
    DocumentSection,
    DocumentStatus,
    Evaluation,
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
    User,
)

client = TestClient(app)

SUB_OBJECTIVES = ["Nêu định nghĩa", "Phân biệt hai trường hợp", "Giải thích vai trò"]
LESSON = "## Phần một\n- ý"

OUTPUTS = {
    PlannerOutput: PlannerOutput(concepts=[Concept(name="Khái niệm", priority=1, summary="tóm tắt")]),
    LearningObjective: LearningObjective(
        bloom_level="understand", verb="giải thích", objective_text="Giải thích khái niệm",
        sub_objectives=SUB_OBJECTIVES,
    ),
    RelevanceCheck: RelevanceCheck(confidence="high", reason="đủ"),
    ContentOutput: ContentOutput(content=LESSON, source_ids=[]),
    LessonText: LessonText(content=LESSON),
    SlidesOutput: SlidesOutput(slides=[Slide(title="S1", bullets=["b"]), Slide(title="S2", bullets=["b"])]),
    ExampleOutput: ExampleOutput(examples=[Example(context="bối cảnh", analysis="phân tích")] * 2),
    QuizQuestion: QuizQuestion(
        common_misconceptions=["m1", "m2", "m3"], stem="Hỏi", reasoning="r",
        choices=["pa một", "pa hai", "pa ba", "pa bốn"], correct_index=1, explanation="lời giải",
    ),
}

# Steps only the RAG arm runs: the no-RAG arm reuses the paired module's objective and does not search.
RAG_ONLY = {"PlannerOutput", "LearningObjective", "RelevanceCheck"}


@pytest.fixture
def llm(monkeypatch):
    calls: list[dict] = []

    def fake(prompt, response_model, system_instruction=None, **kwargs):
        calls.append({"model": response_model.__name__, "prompt": prompt, "system": system_instruction or ""})
        if response_model in OUTPUTS:
            return OUTPUTS[response_model]
        return response_model(**{
            name: True if name.endswith("_pass") else "đạt" for name in response_model.model_fields
        })

    for info in pkgutil.iter_modules(generation.__path__):
        module = importlib.import_module(f"app.generation.{info.name}")
        if hasattr(module, "complete_structured"):
            monkeypatch.setattr(module, "complete_structured", fake)
    return calls


@pytest.fixture
def document(monkeypatch):
    db = SessionLocal()
    try:
        user = User(email=f"nr-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x",
                    is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="No-RAG", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.pdf", status=DocumentStatus.embedded, raw_text="text")
        other = Document(course_id=course.id, filename="b.pdf", status=DocumentStatus.embedded, raw_text="text")
        db.add_all([doc, other])
        db.flush()
        db.add_all(Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=f"đoạn {i}") for i in range(3))
        db.commit()
        ids = {"user": user.id, "course": course.id, "doc": doc.id, "other": other.id}
    finally:
        db.close()

    searches: list[str] = []

    def fake_retrieve(db, course_id, query, **kwargs):
        searches.append(query)
        return db.query(Chunk).filter(Chunk.course_id == course_id).order_by(Chunk.chunk_index).all()

    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(quiz_graph, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "expand_with_parent_context", lambda db, chunks, **kwargs: chunks)
    ids["searches"] = searches

    yield ids

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _generate(document, **kwargs) -> uuid.UUID:
    db = SessionLocal()
    try:
        paired_id = kwargs.pop("paired_module_id", None)
        paired = db.get(Module, paired_id) if paired_id else None
        module = pipeline.run_module_generation(db, db.get(Document, document["doc"]), paired_module=paired, **kwargs)
        return module.id
    finally:
        db.close()


def _pair(document, llm, self_check=True) -> tuple[uuid.UUID, uuid.UUID, list[dict], list[dict]]:
    """(RAG module, no-RAG module, RAG calls, no-RAG calls)."""
    rag_id = _generate(document, self_check=self_check)
    rag_calls = list(llm)
    llm.clear()
    control_id = _generate(document, self_check=self_check, use_retrieval=False, paired_module_id=rag_id)
    return rag_id, control_id, rag_calls, list(llm)


def test_without_retrieval_nothing_is_ever_searched(document, llm, monkeypatch):
    rag_id = _generate(document)

    def no_search(*args, **kwargs):
        raise AssertionError("use_retrieval=False must not search")

    for name in ("retrieve", "expand_with_parent_context", "corrective_retrieve", "run_planner"):
        monkeypatch.setattr(pipeline, name, no_search)
    monkeypatch.setattr(quiz_graph, "retrieve", no_search)
    llm.clear()

    _generate(document, use_retrieval=False, paired_module_id=rag_id)

    assert llm and not {c["model"] for c in llm} & RAG_ONLY


def test_both_arms_store_the_same_components_with_the_same_shape(document, llm):
    rag_id, control_id, _, _ = _pair(document, llm)

    db = SessionLocal()
    try:
        def components(module_id):
            rows = db.query(ModuleComponent).filter(ModuleComponent.module_id == module_id).all()
            return sorted((c.type.value, sorted(c.content), len(c.questions)) for c in rows)

        assert components(rag_id) == components(control_id)
        assert [t for t, _, _ in components(rag_id)] == [
            "content", "example", "explanation", "practice", "quiz", "slides",
        ]
        control = db.query(ModuleComponent).filter(
            ModuleComponent.module_id == control_id, ModuleComponent.type == "content",
        ).one().content
        assert control["source_locators"] == [] and control["claim_citations"] == []
        assert control["relevance_confidence"] is None
    finally:
        db.close()


def test_the_control_takes_the_title_objective_and_section_of_its_paired_module(document, llm):
    rag_id, control_id, _, control_calls = _pair(document, llm)

    db = SessionLocal()
    try:
        rag, control = db.get(Module, rag_id), db.get(Module, control_id)
        for field in ("document_id", "section_id", "title", "learning_objective", "bloom_level", "sub_objectives"):
            assert getattr(rag, field) == getattr(control, field)
    finally:
        db.close()
    # The practice question asks about the same concept the RAG arm searched for.
    assert any("ĐIỀU CẦN KIỂM: Khái niệm: tóm tắt\nMODE: practice" in c["prompt"] for c in control_calls)


_SOURCE_CHUNKS = re.compile(r"SOURCE CHUNK \[S\d+\]:.*?--- END SOURCE CHUNK DATA ---\n\n", re.DOTALL)
_CITATIONS = re.compile(r"CLAIM_CITATIONS \(.*?\):\n.*?\n\n", re.DOTALL)


def _without_sources(prompt: str) -> str:
    return _SOURCE_CHUNKS.sub("", _CITATIONS.sub("", prompt))


def test_the_two_arms_prompts_differ_only_in_the_source_block(document, llm):
    _, _, rag_calls, control_calls = _pair(document, llm)
    rag_calls = [c for c in rag_calls if c["model"] not in RAG_ONLY]

    assert len(rag_calls) == len(control_calls)
    sourced = 0
    for rag, control in zip(rag_calls, control_calls, strict=True):
        sourced += "SOURCE CHUNK" in rag["prompt"]
        assert "SOURCE CHUNK" not in control["prompt"]
        # The questions read the lesson where the RAG arm reads the passages found for them.
        lesson = f"NỘI DUNG BÀI HỌC:\n{LESSON}\n\n"
        assert _without_sources(rag["prompt"]) == control["prompt"].replace(lesson, "")
    # Content, its check, Example, and every question with its check carried passages in the RAG arm.
    assert sourced == 2 + 1 + 4 * 2


STEP_BODIES = {
    "ContentOutput": content._CONTENT_BODY,
    "ContentVerification": content._CONTENT_VERIFY_BODY,
    "ExampleOutput": example._EXAMPLE_BODY,
    "QuizQuestion": quiz_graph._QUESTION_BODY,
    "GroundednessAnswerabilityCheck": quiz_graph._QUESTION_CHECK_BODY,
}


def test_each_step_sends_both_arms_the_same_instruction_but_for_the_source_rules(document, llm):
    _, _, rag_calls, control_calls = _pair(document, llm)
    rag_calls = [c for c in rag_calls if c["model"] not in RAG_ONLY]

    seen = set()
    for rag, control in zip(rag_calls, control_calls, strict=True):
        body = STEP_BODIES.get(rag["model"])
        if body is None:
            assert rag["system"] == control["system"]
            continue
        seen.add(rag["model"])
        assert rag["system"].startswith(IGNORE_EMBEDDED_INSTRUCTIONS + body)
        assert control["system"].startswith(body)
        source_rules = rag["system"].removeprefix(IGNORE_EMBEDDED_INSTRUCTIONS + body)
        no_source_rules = control["system"].removeprefix(body)
        assert source_rules != no_source_rules and "SOURCE CHUNK" not in no_source_rules
    assert seen == set(STEP_BODIES)


def test_the_control_logs_no_source_based_evaluation(document, llm):
    _, control_id, _, _ = _pair(document, llm)

    db = SessionLocal()
    try:
        criteria = {e.criterion for e in db.query(Evaluation).filter(Evaluation.module_id == control_id)}
    finally:
        db.close()
    assert criteria == {
        "quiz_1_answerability_pass", "quiz_2_answerability_pass", "quiz_3_answerability_pass",
        "practice_1_answerability_pass",
    }


def test_without_retrieval_a_paired_module_is_required(document, llm):
    db = SessionLocal()
    try:
        with pytest.raises(ValueError, match="đi cặp"):
            pipeline.run_module_generation(db, db.get(Document, document["doc"]), use_retrieval=False)
    finally:
        db.close()
    assert llm == []


def test_the_worker_runs_a_queued_no_rag_job_with_its_paired_module(document, llm):
    rag_id = _generate(document)
    db = SessionLocal()
    try:
        job = Job(job_type=JobType.module_generation, step="generate", status=JobStatus.running,
                  document_id=document["doc"],
                  payload={"self_check": True, "use_retrieval": False, "paired_module_id": str(rag_id)})
        db.add(job)
        db.commit()
        job_id = job.id
    finally:
        db.close()
    document["searches"].clear()

    pipeline.run_module_generation_step(type("J", (), {"id": job_id})())

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        control = db.get(Module, job.module_id)
        assert job.status == JobStatus.done and control.id != rag_id
        assert control.learning_objective == db.get(Module, rag_id).learning_objective
    finally:
        db.close()
    assert document["searches"] == []


# --- POST /modules/generate --------------------------------------------------------------------


@pytest.fixture
def api(document, monkeypatch):
    monkeypatch.setattr(catalog, "fetch_model_info", lambda: [
        {"model_name": "groq-pro", "litellm_params": {"model": "groq/x"}, "model_info": {"mode": "chat"}},
    ])
    monkeypatch.setattr(catalog, "probe", lambda alias: None)
    catalog.clear_cache()
    db = SessionLocal()
    try:
        module = Module(document_id=document["doc"], course_id=document["course"], title="Khái niệm",
                        learning_objective="Giải thích khái niệm", bloom_level="understand",
                        sub_objectives=SUB_OBJECTIVES)
        db.add(module)
        db.flush()
        db.add(Job(job_type=JobType.module_generation, step="generate", status=JobStatus.done,
                   document_id=document["doc"], module_id=module.id,
                   payload={"self_check": False, "model_alias": "groq-pro"}))
        db.commit()
        document["module"] = module.id
    finally:
        db.close()
    yield document
    catalog.clear_cache()


def _post(api, body, document_id=None):
    return client.post(
        f"/modules/generate?document_id={document_id or api['doc']}", json=body,
        headers={"Authorization": f"Bearer {create_access_token(str(api['user']))}"},
    )


def _no_rag_job(api) -> Job:
    db = SessionLocal()
    try:
        return db.query(Job).filter(Job.document_id == api["doc"], Job.module_id.is_(None)).one()
    finally:
        db.close()


def test_a_no_rag_job_is_queued_with_the_paired_module_model_and_self_check(api):
    response = _post(api, {"use_retrieval": False, "paired_module_id": str(api["module"])})

    assert response.status_code == 200, response.text
    job = _no_rag_job(api)
    assert job.payload == {
        "self_check": False, "output_language": "vi", "model_alias": "groq-pro", "use_retrieval": False,
        "paired_module_id": str(api["module"]),
    }
    assert job.section_id is None


def test_an_explicit_self_check_overrides_the_paired_one(api):
    response = _post(api, {"use_retrieval": False, "paired_module_id": str(api["module"]), "self_check": True})

    assert response.status_code == 200, response.text
    assert _no_rag_job(api).payload["self_check"] is True


def test_a_no_rag_job_follows_the_section_of_its_paired_module(api):
    db = SessionLocal()
    try:
        section = DocumentSection(document_id=api["doc"], title="Mục 1", level=1, position=0,
                                  first_chunk_index=0, last_chunk_index=2)
        db.add(section)
        db.flush()
        db.get(Module, api["module"]).section_id = section.id
        db.commit()
        section_id = section.id
    finally:
        db.close()

    response = _post(api, {"use_retrieval": False, "paired_module_id": str(api["module"])})

    assert response.status_code == 200, response.text
    assert _no_rag_job(api).section_id == section_id


@pytest.mark.parametrize("body", [
    {"use_retrieval": False},
    {"paired_module_id": "PAIRED"},
    {"use_retrieval": False, "paired_module_id": "PAIRED", "section_ids": [str(uuid.uuid4())]},
])
def test_an_inconsistent_no_rag_request_is_refused(api, body):
    body = {k: (str(api["module"]) if v == "PAIRED" else v) for k, v in body.items()}

    response = _post(api, body)

    assert response.status_code == 422, response.text
    assert _no_rag_count(api) == 0


def _no_rag_count(api) -> int:
    db = SessionLocal()
    try:
        return db.query(Job).filter(Job.document_id == api["doc"], Job.module_id.is_(None)).count()
    finally:
        db.close()


def test_a_paired_module_of_another_document_is_refused(api):
    response = _post(api, {"use_retrieval": False, "paired_module_id": str(api["module"])}, api["other"])

    assert response.status_code == 422, response.text


def test_a_paired_module_without_a_learning_objective_is_refused(api):
    db = SessionLocal()
    try:
        db.get(Module, api["module"]).sub_objectives = None
        db.commit()
    finally:
        db.close()

    response = _post(api, {"use_retrieval": False, "paired_module_id": str(api["module"])})

    assert response.status_code == 422, response.text
    assert "Learning Objective" in response.json()["detail"]


def test_a_paired_module_that_is_itself_a_control_is_refused(api):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.module_id == api["module"]).one()
        job.payload = {**job.payload, "use_retrieval": False}
        db.commit()
    finally:
        db.close()

    response = _post(api, {"use_retrieval": False, "paired_module_id": str(api["module"])})

    assert response.status_code == 422, response.text



def test_control_reuses_the_paired_output_language(api):
    with SessionLocal() as db:
        job = db.query(Job).filter(Job.module_id == api["module"]).one()
        job.payload = {**job.payload, "output_language": "en"}
        db.commit()
    response = _post(api, {"use_retrieval": False, "paired_module_id": str(api["module"]), "output_language": "vi"})
    assert response.status_code == 200, response.text
    assert _no_rag_job(api).payload["output_language"] == "en"


def test_control_reuses_paired_custom_instructions(api):
    with SessionLocal() as db:
        job = db.query(Job).filter(Job.module_id == api["module"]).one()
        job.payload = {**job.payload, "custom_instructions": "Explain IDS"}
        db.commit()
    response = _post(api, {"use_retrieval": False, "paired_module_id": str(api["module"]),
                           "custom_instructions": "Ignore IDS"})
    assert response.status_code == 200, response.text
    assert _no_rag_job(api).payload["custom_instructions"] == "Explain IDS"


def test_each_arm_persists_actual_served_models_and_api_exposes_them(document, monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    import litellm

    from app.llm import gateway

    served = "gemini-3.5-flash"
    def completion(**kwargs):
        output = OUTPUTS[ContentOutput if isinstance(kwargs["response_format"], dict) else kwargs["response_format"]]
        return SimpleNamespace(model=served, choices=[
            SimpleNamespace(message=SimpleNamespace(content=json.dumps(output.model_dump()))),
        ])
    monkeypatch.setattr(litellm, "completion", completion)
    with gateway.using_model("gemini-fast"):
        rag_id = _generate(document, self_check=False)
        served = "gpt-4o-mini"
        control_id = _generate(document, self_check=False, use_retrieval=False, paired_module_id=rag_id)
    db = SessionLocal()
    try:
        rag, control = db.get(Module, rag_id), db.get(Module, control_id)
        assert {c["served_model"] for c in rag.generation_models} == {"gemini-3.5-flash"}
        assert {c["served_model"] for c in control.generation_models} == {"gpt-4o-mini"}
        assert {c["requested_alias"] for c in control.generation_models} == {"gemini-fast"}
        db.add_all([
            Job(job_type=JobType.module_generation, step="generate", status=JobStatus.done,
                document_id=rag.document_id, module_id=rag.id,
                payload=pipeline.generation_payload("gemini-fast", False)),
            Job(job_type=JobType.module_generation, step="generate", status=JobStatus.done,
                document_id=control.document_id, module_id=control.id,
                payload=pipeline.control_payload(rag.id, "gemini-fast", False)),
        ])
        db.commit()
        from app.generation.study_packets import StudyFile
        from scripts.export_study_pair import main as export_pair

        export_pair([str(rag_id), "--out", str(tmp_path)])
        for group, saved in (("rag_llm", rag), ("llm_no_rag", control)):
            study = StudyFile.model_validate_json((tmp_path / f"{rag_id}-{group}.json").read_text())
            assert study.generation_models == saved.generation_models
            assert "generation_models" not in study.packet.model_dump()
        response = client.get(f"/modules/{control_id}", headers={
            "Authorization": f"Bearer {create_access_token(str(document['user']))}",
        })
        assert response.status_code == 200
        assert response.json()["generation_models"] == control.generation_models
    finally:
        db.close()
