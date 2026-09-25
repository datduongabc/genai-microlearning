"""Cặp gói nghiên cứu cho một module RAG: gói của chính module đó (nhóm rag_llm) và gói do nhánh không
RAG sinh trên cùng Learning Objective (nhóm llm_no_rag).

Hai gói cùng chủ đề, cùng Learning Objective và cùng một bộ tài liệu đối chiếu cho người và mô hình
chấm. Bộ tài liệu đó lấy bằng một lượt truy hồi độc lập với cả hai bản, chạy lại đúng câu truy vấn
mà pipeline RAG đã dùng để đặt Learning Objective, trong đúng phạm vi mục: lấy chính các đoạn module
RAG đã trích dẫn thì bản RAG được chấm trên những gì nó tự chọn, và được lợi.

Gói chỉ chứa nội dung, không chứa gì lộ nhóm hay nguồn (tên tệp, tên môn, tên model); mã ẩn danh do
phân hệ nghiên cứu gán khi nạp gói.
"""

from sqlalchemy.orm import Session

from app.generation import no_rag
from app.generation.evaluation import (
    ExampleInput,
    ModuleContentInput,
    QuizQuestionInput,
    SlideInput,
)
from app.generation.objective import LearningObjective
from app.llm.gateway import using_model
from app.models import ComponentType, DocumentSection, Module
from app.retrieval.search import ChunkScope, retrieve

# Cùng số đoạn tối thiểu mà pipeline RAG lấy khi đặt Learning Objective.
REFERENCE_MIN_K = 8


class IncompleteModuleError(ValueError):
    """Module thiếu một phần mà gói nghiên cứu bắt buộc phải có, ví dụ quiz không sinh được câu nào."""


def _concept_query(module: Module, section: DocumentSection | None) -> str:
    """Câu truy vấn pipeline RAG đã dùng: "tên khái niệm: tóm tắt" của Planner. Không tìm lại được
    khái niệm (Planner đã chạy lại, đổi tên) thì dùng tên module cùng Learning Objective."""
    holder = section if section is not None else module.document
    for concept in holder.planner_concepts or []:
        if concept.get("name") == module.title:
            return f"{concept['name']}: {concept['summary']}"
    return f"{module.title}: {module.learning_objective or ''}"


def reference_chunks(db: Session, module: Module) -> list[str]:
    section = db.get(DocumentSection, module.section_id) if module.section_id else None
    scope = ChunkScope(module.document_id, section.first_chunk_index, section.last_chunk_index) if section else None
    chunks = retrieve(db, module.course_id, _concept_query(module, section), top_k=None, min_k=REFERENCE_MIN_K, scope=scope)
    return [c.content for c in chunks]


def learning_objective_of(module: Module) -> LearningObjective:
    if not module.learning_objective or not module.bloom_level or not module.sub_objectives:
        raise IncompleteModuleError("Module chưa có đủ Learning Objective, mức Bloom và sub-objective.")
    # verb chỉ dùng khi sinh Learning Objective; ở đây Learning Objective đã có sẵn.
    return LearningObjective(
        bloom_level=module.bloom_level, verb="", objective_text=module.learning_objective,
        sub_objectives=list(module.sub_objectives),
    )


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


def rag_packet(module: Module, sources: list[str]) -> ModuleContentInput:
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


def no_rag_packet(module: Module, generated: no_rag.NoRagModule, sources: list[str]) -> ModuleContentInput:
    def as_input(asked: no_rag.AskedQuestion, explanation: str) -> QuizQuestionInput:
        q = asked.question
        return QuizQuestionInput(stem=q.stem, choices=q.choices, correct_index=q.correct_index, explanation=explanation)

    pairs = list(zip(generated.questions, generated.explanations, strict=True))
    return ModuleContentInput(
        anon_id="",
        topic=module.title,
        bloom_level=module.bloom_level,
        learning_objective=module.learning_objective,
        sub_objectives=list(module.sub_objectives or []),
        content_markdown=generated.content,
        slides=[SlideInput(title=s.title, bullets=s.bullets) for s in generated.slides.slides],
        examples=[ExampleInput(context=e.context, analysis=e.analysis) for e in generated.examples.examples],
        quiz=[as_input(q, e) for q, e in pairs if q.mode == "quiz"],
        practice=next(as_input(q, e) for q, e in pairs if q.mode == "practice"),
        source_chunks=sources,
    )


def study_pair(db: Session, module: Module, model_alias: str | None) -> tuple[ModuleContentInput, ModuleContentInput]:
    """(gói rag_llm, gói llm_no_rag). `model_alias` là model đã sinh module RAG: bản đối chứng phải
    dùng đúng model đó, không thì khác biệt điểm lẫn cả khác biệt giữa hai model."""
    lo = learning_objective_of(module)
    sources = reference_chunks(db, module)
    rag = rag_packet(module, sources)
    section = db.get(DocumentSection, module.section_id) if module.section_id else None
    with using_model(model_alias):
        generated = no_rag.generate_module(lo, practice_target=_concept_query(module, section))
    return rag, no_rag_packet(module, generated, sources)
