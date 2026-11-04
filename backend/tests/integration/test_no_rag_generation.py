"""Nhánh đối chứng llm_no_rag (pipeline chung với `use_retrieval=False`) và cặp gói nghiên cứu: bản
không RAG chỉ khác bản RAG ở chỗ không thấy tài liệu nào, còn chủ đề, Learning Objective, cấu trúc, model và tài liệu đối chiếu khi chấm thì như
nhau, để khác biệt điểm quy được về RAG.

Mọi lệnh gọi mô hình được thay bằng hàm giả ghi lại prompt và model đang dùng; truy hồi cũng được
thay, nên không tốn hạn mức nào."""

import importlib
import json
import pkgutil
import re
import types
import uuid

import pytest

from app import generation
from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.generation import quiz_graph, study_packets
from app.generation.content import LessonText, LessonVerification
from app.generation.example import Example, ExampleOutput
from app.generation.quiz_graph import QuizQuestion
from app.generation.slides import Slide, SlidesOutput
from app.llm import gateway
from app.models import (
    ComponentType,
    Course,
    Document,
    DocumentStatus,
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
    Question,
    User,
)
from app.retrieval.search import ChunkScope
from scripts import export_study_pair
from scripts.export_study_pair import model_that_made
from tests.integration.generation_helpers import passing_check

SOURCES = ["NGUON-BI-MAT-1: một đoạn của giáo trình", "NGUON-BI-MAT-2: đoạn khác"]
COURSE_NAME = "TEN-MON-HOC-KHONG-DUOC-LO"
FILENAME = "ten-tep-khong-duoc-lo.pdf"


@pytest.fixture
def llm(monkeypatch):
    """Mô hình giả cho nhánh không RAG: mọi bước đều đạt trừ khi test đặt `fail` cho một schema."""
    calls: list[dict] = []
    fail: dict[type, list] = {}

    def fake(prompt, response_model, system_instruction=None, **kwargs):
        calls.append({"model": response_model, "prompt": prompt, "system": system_instruction or "",
                      "alias": gateway.active_model()})
        queued = fail.get(response_model)
        if queued:
            return queued.pop(0)
        if response_model is LessonText:
            return LessonText(content="## Phần một\n- ý\n## Phần hai\n- ý")
        if response_model is SlidesOutput:
            return SlidesOutput(slides=[Slide(title="S1", bullets=["b"]), Slide(title="S2", bullets=["b"])])
        if response_model is ExampleOutput:
            return ExampleOutput(examples=[Example(context="bối cảnh", analysis="phân tích")] * 2)
        if response_model is QuizQuestion:
            target = re.search(r"ĐIỀU CẦN KIỂM: (.*)", prompt).group(1)
            return QuizQuestion(common_misconceptions=["m1", "m2", "m3"], stem=f"Hỏi về {target}",
                                reasoning="r", choices=["pa một", "pa hai", "pa ba", "pa bốn"], correct_index=1,
                                explanation="lời giải")
        # Mọi bản kiểm (Verification/Check): đạt hết.
        return passing_check(response_model)

    for info in pkgutil.iter_modules(generation.__path__):
        module = importlib.import_module(f"app.generation.{info.name}")
        if hasattr(module, "complete_structured"):
            monkeypatch.setattr(module, "complete_structured", fake)
    return types.SimpleNamespace(calls=calls, fail=fail)


@pytest.fixture
def rag_module(monkeypatch):
    """Một module RAG đã sinh xong, và truy hồi giả trả SOURCES cho câu truy vấn đối chiếu."""
    asked = []

    def fake_retrieve(db, course_id, query, top_k=8, candidate_k=30, min_k=2, scope=None):
        asked.append({"query": query, "scope": scope, "min_k": min_k})
        return [types.SimpleNamespace(content=text) for text in SOURCES]

    monkeypatch.setattr(study_packets, "retrieve", fake_retrieve)

    def no_retrieval(*args, **kwargs):
        raise AssertionError("the control must not retrieve anything")

    # Only the reference set is retrieved; the control module itself never searches.
    monkeypatch.setattr(pipeline, "retrieve", no_retrieval)
    monkeypatch.setattr(quiz_graph, "retrieve", no_retrieval)
    monkeypatch.setattr(pipeline, "expand_with_parent_context", no_retrieval)
    monkeypatch.setattr(pipeline, "run_planner", no_retrieval)

    db = SessionLocal()
    try:
        user = User(email=f"study-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x",
                    is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name=COURSE_NAME, owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename=FILENAME, status=DocumentStatus.embedded,
                       planner_concepts=[{"name": "Chủ đề X", "priority": 1, "summary": "tóm tắt của planner"}])
        db.add(doc)
        db.flush()
        module = Module(document_id=doc.id, course_id=course.id, title="Chủ đề X",
                        learning_objective="Sau khi học, người học có thể giải thích X.", bloom_level="understand",
                        sub_objectives=["mục tiêu con một", "mục tiêu con hai"])
        db.add(module)
        db.flush()

        def component(type_, content, questions=()):
            c = ModuleComponent(module_id=module.id, type=type_, content=content)
            db.add(c)
            db.flush()
            for position, (stem, explanation) in enumerate(questions):
                db.add(Question(component_id=c.id, position=position, stem=stem,
                                choices={"options": ["A", "B", "C", "D"]}, answer_key="2", explanation=explanation))

        component(ComponentType.content, {"markdown": "## RAG content"})
        component(ComponentType.slides, {"slides": [{"title": "T", "bullets": ["b"]}]})
        component(ComponentType.example, {"examples": [{"context": "c", "analysis": "a"}]})
        component(ComponentType.quiz, {"questions": [{}, {}]}, [("RAG-Q1", "gốc 1"), ("RAG-Q2", "gốc 2")])
        component(ComponentType.practice, {"questions": [{}]}, [("RAG-P1", "gốc p")])
        component(ComponentType.explanation, {"items": [
            {"component": "quiz", "position": 1, "reasoning": "r", "explanation_text": "ĐÃ-SỬA-2"},
        ]})
        db.add(Job(job_type=JobType.module_generation, step="generate", status=JobStatus.done,
                   document_id=doc.id, module_id=module.id, payload={"model_alias": "groq-pro"}))
        db.commit()
        ids = {"user": user.id, "course": course.id, "module": module.id, "document": doc.id}
    finally:
        db.close()

    yield types.SimpleNamespace(ids=ids, asked=asked)

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _pair(rag_module, alias="groq-pro", self_check=True):
    db = SessionLocal()
    try:
        return study_packets.study_pair(db, db.get(Module, rag_module.ids["module"]), alias, self_check)
    finally:
        db.close()


def test_no_source_passage_reaches_any_prompt_of_the_control(rag_module, llm):
    _, control = _pair(rag_module)

    assert llm.calls
    for call in llm.calls:
        assert "NGUON-BI-MAT" not in call["prompt"] and "NGUON-BI-MAT" not in call["system"]
    assert control.source_chunks == SOURCES


def test_both_packets_share_topic_objective_and_the_reference_the_judge_reads(rag_module, llm):
    rag, control = _pair(rag_module)

    for field in ("topic", "learning_objective", "bloom_level", "sub_objectives", "source_chunks"):
        assert getattr(rag, field) == getattr(control, field)


def test_the_reference_is_an_independent_retrieval_with_the_query_the_rag_objective_was_made_from(rag_module, llm):
    _pair(rag_module)

    assert rag_module.asked == [
        {"query": "Chủ đề X: tóm tắt của planner", "scope": ChunkScope(rag_module.ids["document"]), "min_k": 8}
    ]


def test_the_control_is_never_checked_against_sources(rag_module, llm):
    _pair(rag_module)

    checked = {name for call in llm.calls for name in call["model"].model_fields if name.endswith("_pass")}
    assert checked and not {name for name in checked if "grounded" in name or "style" in name}


def test_the_control_has_the_same_structure_one_question_per_sub_objective_and_a_practice(rag_module, llm):
    _, control = _pair(rag_module)

    assert [q.stem for q in control.quiz] == ["Hỏi về mục tiêu con một", "Hỏi về mục tiêu con hai"]
    assert control.practice.stem.startswith("Hỏi về")
    assert all(q.explanation == "lời giải" for q in [*control.quiz, control.practice])
    # Lời giải là thứ câu hỏi sinh kèm, không tốn lệnh gọi mô hình riêng.
    assert not [c for c in llm.calls if "Explanation" in c["model"].__name__]
    assert control.examples and control.slides


def test_the_control_is_generated_with_the_model_given_for_the_pair(rag_module, llm):
    _pair(rag_module, alias="groq-pro")

    assert {call["alias"] for call in llm.calls} == {"groq-pro"}


def test_the_model_of_the_pair_is_the_one_that_made_the_rag_module(rag_module):
    db = SessionLocal()
    try:
        assert model_that_made(db, rag_module.ids["module"]) == "groq-pro"
        assert model_that_made(db, uuid.uuid4()) is None
    finally:
        db.close()


def test_each_exported_file_records_the_model_that_made_its_own_packet(rag_module, llm, tmp_path):
    """--model chỉ đổi model của bản không RAG; bản RAG vẫn do model trong job sinh module làm ra, và
    bước chấm dựa vào đó để từ chối mô hình chấm cùng hãng."""
    module_id = rag_module.ids["module"]

    export_study_pair.main([str(module_id), "--out", str(tmp_path), "--model", "claude-fast"])

    def generator(group):
        path = tmp_path / f"{module_id}-{group}.json"
        return study_packets.StudyFile.model_validate_json(path.read_text(encoding="utf-8")).generator_model

    assert generator("rag_llm") == "groq-pro"
    assert generator("llm_no_rag") == "claude-fast"
    assert {call["alias"] for call in llm.calls} == {"claude-fast"}


def test_the_rag_packet_keeps_question_order_and_the_teachers_edited_explanation(rag_module, llm):
    rag, _ = _pair(rag_module)

    assert [q.stem for q in rag.quiz] == ["RAG-Q1", "RAG-Q2"]
    assert [q.explanation for q in rag.quiz] == ["gốc 1", "ĐÃ-SỬA-2"]
    assert rag.practice.stem == "RAG-P1" and rag.quiz[0].correct_index == 2


def test_neither_packet_carries_anything_that_reveals_its_group_or_source(rag_module, llm):
    rag, control = _pair(rag_module)

    for packet in (rag, control):
        dumped = json.dumps(packet.model_dump(), ensure_ascii=False)
        for secret in (COURSE_NAME, FILENAME, "groq-pro", "rag", "no_rag"):
            assert secret not in dumped


def test_a_module_without_quiz_questions_is_refused_before_any_generation(rag_module, llm):
    db = SessionLocal()
    try:
        module = db.get(Module, rag_module.ids["module"])
        quiz = next(c for c in module.components if c.type == ComponentType.quiz)
        for q in list(quiz.questions):
            db.delete(q)
        db.commit()
        with pytest.raises(study_packets.IncompleteModuleError):
            study_packets.study_pair(db, db.get(Module, rag_module.ids["module"]), None)
    finally:
        db.close()

    assert llm.calls == []


def test_a_failed_control_check_is_retried_with_its_reason(rag_module, llm):
    llm.fail[LessonVerification] = [LessonVerification(
        completeness_pass=False, completeness_reason="LY-DO-THIEU-PHAN-HAI", scope_pass=True, scope_reason="ok",
    )]

    _pair(rag_module)

    content_prompts = [c["prompt"] for c in llm.calls if c["model"] is LessonText]
    assert len(content_prompts) == 2
    assert "LY-DO-THIEU-PHAN-HAI" not in content_prompts[0] and "LY-DO-THIEU-PHAN-HAI" in content_prompts[1]


def _checks(llm) -> list[str]:
    return [c["model"].__name__ for c in llm.calls if any(n.endswith("_pass") for n in c["model"].model_fields)]


def test_only_content_and_questions_of_the_control_are_checked_like_the_rag_ones(rag_module, llm):
    """Hai sub-objective: Content, Slides, Example, rồi ba câu (hai quiz, một practice), mỗi câu một
    lượt sinh và một lượt kiểm; Content một lượt kiểm. Slides và Example không có lượt kiểm nào."""
    _pair(rag_module)

    assert _checks(llm) == ["LessonVerification"] + ["AnswerabilityCheck"] * 3
    assert len(llm.calls) == 3 + 1 + 3 * 2


def test_without_self_check_the_control_makes_no_check_call_at_all(rag_module, llm):
    _, control = _pair(rag_module, self_check=False)

    assert _checks(llm) == []
    assert [c["model"].__name__ for c in llm.calls] == [
        "LessonText", "SlidesOutput", "ExampleOutput", "QuizQuestion", "QuizQuestion", "QuizQuestion",
    ]
    assert len(control.quiz) == 2 and control.practice.stem.startswith("Hỏi về")


def test_the_export_follows_the_self_check_flag_the_rag_module_was_made_with(rag_module, llm, tmp_path):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.module_id == rag_module.ids["module"]).one()
        job.payload = {**job.payload, "self_check": False}
        db.commit()
    finally:
        db.close()

    export_study_pair.main([str(rag_module.ids["module"]), "--out", str(tmp_path)])

    assert _checks(llm) == []


def _controls_of(rag_module) -> list[Job]:
    db = SessionLocal()
    try:
        return [
            j for j in db.query(Job).filter(Job.module_id.is_not(None))
            if j.payload.get("paired_module_id") == str(rag_module.ids["module"])
        ]
    finally:
        db.close()


def test_the_control_is_recorded_with_the_payload_an_api_queued_control_carries(rag_module, llm):
    _pair(rag_module, alias="groq-pro", self_check=False)

    [job] = _controls_of(rag_module)
    assert job.status == JobStatus.done and job.job_type == JobType.module_generation
    assert job.payload == {
        "self_check": False, "output_language": "vi", "model_alias": "groq-pro", "use_retrieval": False,
        "paired_module_id": str(rag_module.ids["module"]),
    }
    db = SessionLocal()
    try:
        assert pipeline.is_control(db, job.module_id)
        assert not pipeline.is_control(db, rag_module.ids["module"])
    finally:
        db.close()


def test_a_later_export_reuses_the_control_for_the_same_model(rag_module, llm):
    _, first = _pair(rag_module)
    calls = len(llm.calls)

    _, again = _pair(rag_module)

    assert len(llm.calls) == calls and again == first
    assert len(_controls_of(rag_module)) == 1

    _pair(rag_module, alias="claude-fast")
    assert len(_controls_of(rag_module)) == 2


def test_a_control_is_refused_as_the_rag_module_of_a_pair(rag_module, llm, tmp_path):
    _pair(rag_module)
    [job] = _controls_of(rag_module)
    llm.calls.clear()

    db = SessionLocal()
    try:
        with pytest.raises(ValueError, match="đối chứng"):
            study_packets.study_pair(db, db.get(Module, job.module_id), "groq-pro")
    finally:
        db.close()
    with pytest.raises(SystemExit, match="đối chứng"):
        export_study_pair.main([str(job.module_id), "--out", str(tmp_path)])

    assert llm.calls == [] and not list(tmp_path.iterdir())
