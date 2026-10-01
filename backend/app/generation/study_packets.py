"""Cặp gói nghiên cứu cho một module RAG: gói của chính module đó (nhóm rag_llm) và gói của module do
chính pipeline đó sinh với `use_retrieval=False` trên cùng Learning Objective (nhóm llm_no_rag).

Hai gói cùng chủ đề, cùng Learning Objective và cùng một bộ tài liệu đối chiếu cho người và mô hình
chấm; quy tắc phạm vi và độ phủ ở docs/rubric.md, mục 7. Không lấy riêng các đoạn module RAG
đã trích dẫn: như vậy bản RAG được chấm trên những gì nó tự chọn, và được lợi.

Gói chỉ chứa nội dung, không chứa gì lộ nhóm hay nguồn (tên tệp, tên môn, tên model); mã ẩn danh do
phân hệ nghiên cứu gán khi nạp gói. Nhóm, mã cặp và model đã sinh nằm ở tệp nghiên cứu bọc ngoài gói
(StudyFile), dành cho người nghiên cứu và bước chấm, không bao giờ đưa cho người hay mô hình chấm.
"""

from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.generation.claim_evaluation import ReferenceEvidence, StoredCitation
from app.generation.evaluation import (
    ExampleInput,
    ModuleContentInput,
    QuizQuestionInput,
    SlideInput,
)
from app.generation.module_generation_pipeline import (
    IncompleteModuleError,
    concept_query_of,
    control_payload,
    generation_payload_of,
    is_control,
    learning_objective_of,
    run_module_generation,
)
from app.llm.gateway import using_model
from app.models import Chunk, ComponentType, DocumentSection, Job, JobStatus, JobType, Module
from app.retrieval.search import ChunkScope, retrieve

# Cùng số đoạn tối thiểu mà pipeline RAG lấy khi đặt Learning Objective.
REFERENCE_MIN_K = 8
REFERENCE_MAX_CHUNKS = 64
REFERENCE_MAX_CHARACTERS = 120_000


class StudyFile(BaseModel):
    """Một tệp của thư mục nghiên cứu: gói đem chấm cùng những gì chỉ người nghiên cứu được biết.
    `generator_model` là alias gateway đã sinh gói, để bước chấm từ chối mô hình chấm cùng hãng."""

    pair_id: str
    group: Literal["rag_llm", "llm_no_rag"]
    generator_model: str
    packet: ModuleContentInput
    reference: ReferenceEvidence = ReferenceEvidence()
    claim_citations: list[StoredCitation] | None = None


def reference_chunks(db: Session, module: Module) -> list[str]:
    section = db.get(DocumentSection, module.section_id) if module.section_id else None
    all_chunks = reference_scope_chunks(db, module, section)
    if small_reference(all_chunks):
        return [c.content for c in all_chunks]
    scope = (
        ChunkScope(module.document_id, section.first_chunk_index, section.last_chunk_index)
        if section else ChunkScope(module.document_id)
    )
    chunks = retrieve(db, module.course_id, concept_query_of(module, section), top_k=None, min_k=REFERENCE_MIN_K, scope=scope)
    return [c.content for c in chunks]


def section_chunks(db: Session, module: Module, section: DocumentSection) -> list[Chunk]:
    return db.query(Chunk).filter(
        Chunk.document_id == module.document_id, Chunk.course_id == module.course_id,
        Chunk.chunk_index >= section.first_chunk_index, Chunk.chunk_index <= section.last_chunk_index,
    ).order_by(Chunk.chunk_index).all()


def reference_scope_chunks(db: Session, module: Module, section: DocumentSection | None) -> list[Chunk]:
    if section:
        return section_chunks(db, module, section)
    if module.section_id is not None:
        return []
    return db.query(Chunk).filter(
        Chunk.document_id == module.document_id, Chunk.course_id == module.course_id,
    ).order_by(Chunk.chunk_index).all()


def small_reference(chunks: list[Chunk]) -> bool:
    return bool(chunks) and len(chunks) <= REFERENCE_MAX_CHUNKS and sum(
        len(c.content) for c in chunks
    ) <= REFERENCE_MAX_CHARACTERS


def study_evidence(db: Session, module: Module, sources: list[str]) -> tuple[ReferenceEvidence, list[StoredCitation] | None]:
    """Snapshot reference coverage and resolve saved locators within this document only.

    Repeated locators are ambiguous: never choose an arbitrary chunk or a supporting neighbour.
    Legacy Content without claim_citations stays missing, distinct from a recorded empty list.
    """
    section = db.get(DocumentSection, module.section_id) if module.section_id else None
    chunks = reference_scope_chunks(db, module, section)
    complete = small_reference(chunks) and sources == [c.content for c in chunks]
    reference = ReferenceEvidence(
        coverage="complete" if complete else "partial" if section else "unknown",
        chunk_ids=[str(c.id) for c in chunks] if complete else [],
        total_chunks=len(chunks) if complete or section else None,
    )
    return reference, resolved_citations(db, module)


def resolved_citations(db: Session, module: Module) -> list[StoredCitation] | None:
    """Resolve saved Content locators within the module's document, without guessing ties."""
    content = _component(module, ComponentType.content)
    saved = content.content.get("claim_citations") if content else None
    if saved is None:
        return None
    candidates = db.query(Chunk).filter(
        Chunk.document_id == module.document_id, Chunk.course_id == module.course_id,
    ).all()
    citations = []
    for item in saved:
        matches = [c for c in candidates if c.source_ref and c.source_ref == item["locator"]]
        chunk = matches[0] if len(matches) == 1 else None
        citations.append(StoredCitation(
            claim=item["claim"], locator=item["locator"],
            chunk_id=str(chunk.id) if chunk else None, chunk_content=chunk.content if chunk else None,
            resolution="resolved" if chunk else "ambiguous" if matches else "missing",
        ))
    return citations


def _component(module: Module, component_type: ComponentType):
    return next((c for c in module.components if c.type == component_type), None)


def _questions(module: Module, component_type: ComponentType) -> list[QuizQuestionInput]:
    component = _component(module, component_type)
    explanation = _component(module, ComponentType.explanation)
    edited = {
        int(item["position"]): item.get("explanation_text") or ""
        for item in ((explanation.content.get("items") or []) if explanation else [])
        if item.get("component") == component_type.value
    }
    return [
        QuizQuestionInput(
            stem=q.stem, choices=list(q.choices.get("options", [])), correct_index=int(q.answer_key),
            explanation=edited.get(q.position) or q.explanation or "",
        )
        for q in (component.questions if component else [])
    ]


def module_packet(module: Module, sources: list[str]) -> ModuleContentInput:
    """Gói của một module đã lưu, như nhau cho cả hai nhóm."""
    quiz = _questions(module, ComponentType.quiz)
    practice = _questions(module, ComponentType.practice)
    if not quiz or not practice:
        raise IncompleteModuleError("Module chưa có câu quiz hoặc câu practice nào, chưa đưa vào nghiên cứu được.")
    content = _component(module, ComponentType.content)
    slides = _component(module, ComponentType.slides)
    examples = _component(module, ComponentType.example)
    return ModuleContentInput(
        anon_id="",
        topic=module.title,
        bloom_level=module.bloom_level,
        learning_objective=module.learning_objective,
        sub_objectives=list(module.sub_objectives or []),
        content_markdown=(content.content.get("markdown") or "") if content else "",
        slides=[SlideInput(**s) for s in ((slides.content.get("slides") or []) if slides else [])],
        examples=[ExampleInput(**e) for e in ((examples.content.get("examples") or []) if examples else [])],
        quiz=quiz,
        practice=practice[0],
        source_chunks=sources,
    )


def _control_of(db: Session, module: Module, model_alias: str | None, self_check: bool) -> Module:
    """Bản đối chứng của `module` với đúng model và cờ tự kiểm này: dùng lại bản đã sinh nếu có, không
    thì sinh mới và ghi một job đã xong mang payload như job đối chứng xếp qua API, để mọi nơi nhận ra
    đó là bản đối chứng."""
    paired_payload = generation_payload_of(db, module.id)
    output_language = paired_payload.get("output_language", "vi")
    custom_instructions = paired_payload.get("custom_instructions", "")
    payload = control_payload(module.id, model_alias, self_check, output_language, custom_instructions)
    existing = (
        db.query(Job)
        .filter(
            Job.job_type == JobType.module_generation, Job.status == JobStatus.done,
            Job.module_id.is_not(None), Job.payload == payload,
        )
        .order_by(Job.created_at.desc())
        .first()
    )
    if existing is not None:
        return db.get(Module, existing.module_id)
    section = db.get(DocumentSection, module.section_id) if module.section_id else None
    with using_model(model_alias):
        control = run_module_generation(
            db, module.document, section, self_check=self_check, use_retrieval=False, paired_module=module,
            output_language=output_language, custom_instructions=custom_instructions,
        )
    db.add(Job(
        job_type=JobType.module_generation, step="generate", status=JobStatus.done, document_id=control.document_id,
        section_id=control.section_id, module_id=control.id, payload=payload,
    ))
    db.commit()
    return control


def study_pair(
    db: Session, module: Module, model_alias: str | None, self_check: bool = True
) -> tuple[ModuleContentInput, ModuleContentInput]:
    """(gói rag_llm, gói llm_no_rag). Bản đối chứng là một module sinh bằng chính pipeline RAG với
    `use_retrieval=False`, lưu như mọi module khác và dùng lại ở các lần xuất sau. `model_alias` là
    model đã sinh module RAG: bản đối chứng phải dùng đúng model đó, không thì khác biệt điểm lẫn cả
    khác biệt giữa hai model. `self_check` cũng phải là cờ đã dùng khi sinh module RAG, vì cùng lý do."""
    if is_control(db, module.id):
        raise ValueError(f"Module {module.id} là bản đối chứng không RAG, không phải module RAG.")
    learning_objective_of(module)
    sources = reference_chunks(db, module)
    rag = module_packet(module, sources)
    return rag, module_packet(_control_of(db, module, model_alias, self_check), sources)
