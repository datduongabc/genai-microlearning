"""Sinh module như 1 job step (job_queue.py) thay vì 1 HTTP request đồng bộ,
chặn -- đã thấy thật: 1 lần sinh mất 10+ phút (17 lệnh gọi LLM tuần tự),
trước đây không có cách nào retry tự động khi gặp RateLimitError giữa
chừng ngoài bấm lại tay (và bấm lại tay từng gây duplicate module trước khi
có lock/queue). Toàn bộ logic sinh giữ NGUYÊN như trước (chuyển từ
app/api/modules.py, không đổi hành vi) -- chỉ đổi cách được gọi và bỏ
`_generating_document_ids` in-memory lock (bảng jobs giờ là nguồn sự thật
duy nhất cho "có đang sinh dở không", không cần 2 cơ chế song song)."""

import uuid

from sqlalchemy.orm import Session

from app.db import WorkerSessionLocal
from app.generation.content import generate_content
from app.generation.example import generate_example
from app.generation.objective import generate_learning_objective
from app.generation.planner import PLANNER_MAX_INPUT_CHARS, Concept, PlannerOutput, run_planner
from app.generation.quiz_graph import generate_explanation, generate_quiz_question
from app.generation.relevance import corrective_retrieve
from app.generation.scope_digest import build_scope_digest
from app.generation.slides import generate_slides
from app.models import (
    Chunk,
    ComponentType,
    Document,
    DocumentSection,
    Evaluation,
    Job,
    JobStatus,
    Module,
    ModuleComponent,
)
from app.models import Question as QuestionModel
from app.retrieval.search import ChunkScope, expand_with_parent_context, retrieve


def _get_or_cache_planner(db: Session, doc: Document, section: DocumentSection | None = None) -> PlannerOutput:
    """run_planner() tu nhan thiet ke "1 lenh goi/tai lieu" nhung truoc day
    bi goi lai MOI LAN generate duoc goi cho cung document -- nguyen nhan
    that gay dao dong bloom_level giua cac lan sinh (xac nhan qua bai test
    lap lai 18 tai lieu that: 3/17 doi ket qua). Cache 1 lan, tai su dung
    moi lan sau.

    Voi 1 muc cua giao trinh, cache nam tren chinh muc do (moi muc la 1 nguon
    khac nhau) va Planner doc ban tom luoc trai deu ca muc, khong phai 8000
    ky tu dau cua ca cuon sach."""
    holder = section if section is not None else doc
    if holder.planner_concepts is not None:
        return PlannerOutput(concepts=[Concept(**c) for c in holder.planner_concepts])

    if section is None:
        source_text = doc.raw_text
    else:
        chunk_texts = [
            text
            for (text,) in db.query(Chunk.content)
            .filter(
                Chunk.document_id == doc.id,
                Chunk.chunk_index.between(section.first_chunk_index, section.last_chunk_index),
            )
            .order_by(Chunk.chunk_index)
        ]
        source_text = build_scope_digest(chunk_texts, PLANNER_MAX_INPUT_CHARS)

    planner_out = run_planner(source_text)
    holder.planner_concepts = [c.model_dump() for c in planner_out.concepts]
    db.commit()
    return planner_out


def _log_hallucination_check(db: Session, module_id: uuid.UUID, criterion: str, passed: bool) -> None:
    db.add(Evaluation(module_id=module_id, criterion=criterion, score=1.0 if passed else 0.0))


def _save_quiz_component(db: Session, module_id: uuid.UUID, quiz_type: ComponentType, quiz_state: dict) -> None:
    question = quiz_state.get("question")
    component = ModuleComponent(
        module_id=module_id,
        type=quiz_type,
        content={
            "passed": quiz_state.get("passed", False),
            "relevance_confidence": quiz_state.get("relevance_confidence"),
            "relevance_retry_count": quiz_state.get("relevance_retry_count", 0),
            "correction_retry_count": quiz_state.get("correction_retry_count", 0),
            "fail_reason": quiz_state.get("fail_reason"),
            # Written before the answer was chosen: kept so a teacher can read why this is the
            # answer, and so the answer can be audited against it.
            "reasoning": question.reasoning if question is not None else None,
        },
    )
    db.add(component)
    db.flush()

    db.add(Evaluation(module_id=module_id, criterion=f"{quiz_type.value}_groundedness_answerability_pass",
                       score=1.0 if quiz_state.get("passed") else 0.0))

    if question is not None:
        db.add(QuestionModel(
            component_id=component.id,
            stem=question.stem,
            choices={"options": question.choices},
            answer_key=str(question.correct_index),
            explanation=question.explanation,
        ))


def run_module_generation(db: Session, doc: Document, section: DocumentSection | None = None) -> Module:
    """Planner -> LO -> Content -> Slides -> Example -> Quiz/Practice ->
    Explanation. Nguyên logic chuyển từ _run_generate_module() cũ
    (app/api/modules.py) -- chỉ đổi return type (Module object, không phải
    dict HTTP response, vì caller giờ là job executor).

    Với `section` (1 chương/mục của giáo trình), mọi bước đều chỉ thấy các
    chunk của mục đó thay vì cả tài liệu."""
    planner_out = _get_or_cache_planner(db, doc, section)
    # A section confines every later step to its own chunks: retrieval, the
    # parent-context window and the quiz all see nothing outside it.
    scope = (
        ChunkScope(doc.id, section.first_chunk_index, section.last_chunk_index) if section is not None else None
    )
    top_concept = min(planner_out.concepts, key=lambda c: c.priority)
    query = f"{top_concept.name}: {top_concept.summary}"
    context_chunks = retrieve(db, doc.course_id, query, top_k=None, min_k=8, scope=scope)

    lo, lo_check = generate_learning_objective(top_concept, context_chunks)

    module = Module(
        document_id=doc.id,
        course_id=doc.course_id,
        section_id=section.id if section is not None else None,
        title=top_concept.name,
        learning_objective=lo.objective_text,
        bloom_level=lo.bloom_level,
        sub_objectives=lo.sub_objectives,
    )
    db.add(module)
    db.flush()
    _log_hallucination_check(db, module.id, "lo_grounded_pass", lo_check.grounded_pass)

    # Retrieve THÊM theo từng sub_objective, không chỉ dùng lại context_chunks
    # đã lấy cho top_concept -- phát hiện thật qua LLM-judge (11 module thật,
    # GD2 Tuần 1): completeness trung bình chỉ 3.45/5, 2/3 module bị điểm
    # thấp nhất (Polymorphism, Searching+heuristics) có ĐIỂM CHUNG rõ ràng:
    # Planner tự mô tả concept đó bao trùm ≥2 nhánh trong chính summary (vd
    # "Searching covers uninformed search... and informed search using
    # heuristics"), nhưng content_context trước đây chỉ retrieve DUY NHẤT 1
    # lần theo query top_concept -- nhánh nào không khớp ngữ nghĩa với query
    # gộp đó bị rớt khỏi ngữ cảnh Content dù sub_objectives (LO tự sinh) đã
    # đúng liệt kê nhánh đó trong phạm vi. Đối chứng: module "Encapsulation"
    # (phạm vi hẹp CÓ CHỦ ĐÍCH, không hứa bao quát rộng) vẫn được chấm đủ
    # điểm completeness đúng theo rubric ("bỏ sót chủ đề khác không liên
    # quan LO thì không trừ điểm") -- nên đây không phải lỗi Planner tách vụn
    # khái niệm, mà là retrieval của Content không theo kịp phạm vi LO đã
    # đúng khai báo. Retrieval không tốn quota LLM (chỉ dense+BM25+rerank cục
    # bộ) nên thêm vài lượt/sub_objective không đáng kể chi phí.
    extra_chunks = []
    seen_chunk_ids = {c.id for c in context_chunks}
    for sub_objective in lo.sub_objectives:
        for c in retrieve(db, doc.course_id, sub_objective, top_k=None, min_k=4, scope=scope):
            if c.id not in seen_chunk_ids:
                extra_chunks.append(c)
                seen_chunk_ids.add(c.id)

    # Corrective RAG before Content: the passages are judged against the objective and its
    # sub-objectives, and a search that missed is rewritten and run again, instead of the lesson
    # being written from whatever the first search happened to return.
    objective_text = "\n".join([lo.objective_text, *(f"- {sub}" for sub in lo.sub_objectives)])
    corrected = corrective_retrieve(
        objective_text,
        context_chunks + extra_chunks,
        lambda refined: retrieve(db, doc.course_id, refined, top_k=None, min_k=8, scope=scope),
    )
    content_context = expand_with_parent_context(db, corrected.chunks, scope=scope)
    content_out, content_check = generate_content(lo, content_context)
    db.add(ModuleComponent(
        module_id=module.id, type=ComponentType.content,
        content={
            "markdown": content_out.content,
            "source_locators": content_out.source_locators,
            "claim_citations": [c.model_dump() for c in content_out.claim_citations],
            "relevance_confidence": corrected.confidence,
            "relevance_retry_count": corrected.retries,
        },
    ))
    _log_hallucination_check(db, module.id, "content_relevance_pass", corrected.confidence == "high")
    _log_hallucination_check(db, module.id, "content_groundedness_pass", content_check.groundedness_pass)

    slides_out, slides_check = generate_slides(lo, content_out.content)
    db.add(ModuleComponent(
        module_id=module.id, type=ComponentType.slides,
        content={"slides": [s.model_dump() for s in slides_out.slides]},
    ))
    _log_hallucination_check(db, module.id, "slides_no_new_info_pass", slides_check.no_new_info_pass)

    example_out, example_check = generate_example(lo, content_out.content, context_chunks)
    db.add(ModuleComponent(
        module_id=module.id, type=ComponentType.example,
        content={"context": example_out.context, "analysis": example_out.analysis},
    ))
    _log_hallucination_check(db, module.id, "example_no_overreach_pass", example_check.no_overreach_pass)

    quiz_state = generate_quiz_question(db, doc.course_id, query, lo, mode="quiz", scope=scope)
    _save_quiz_component(db, module.id, ComponentType.quiz, quiz_state)

    practice_state = generate_quiz_question(db, doc.course_id, query, lo, mode="practice", scope=scope)
    _save_quiz_component(db, module.id, ComponentType.practice, practice_state)

    if quiz_state.get("question") is not None:
        # The passages the question was made and checked against, not those of the objective:
        # the explanation has to stand on the same ground as the answer it explains.
        explanation_out, explanation_check = generate_explanation(quiz_state["question"], quiz_state["chunks"])
        _log_hallucination_check(db, module.id, "explanation_grounded_pass", explanation_check.grounded_pass)
        db.add(ModuleComponent(
            module_id=module.id, type=ComponentType.explanation,
            content={"reasoning": explanation_out.reasoning, "explanation_text": explanation_out.explanation_text},
        ))

    db.commit()
    db.refresh(module)
    return module


def run_module_generation_step(job: Job) -> None:
    db = WorkerSessionLocal()
    try:
        current_job = db.get(Job, job.id)
        doc = db.get(Document, current_job.document_id)
        if doc is None:
            raise ValueError(f"Document {current_job.document_id} not found")
        if not doc.raw_text:
            raise ValueError("Document chưa được parse (raw_text rỗng)")

        section = None
        if current_job.section_id is not None:
            section = db.get(DocumentSection, current_job.section_id)
            if section is None:
                raise ValueError(f"Section {current_job.section_id} not found")

        module = run_module_generation(db, doc, section)

        current_job.module_id = module.id
        current_job.status = JobStatus.done
        db.commit()
    finally:
        db.close()
