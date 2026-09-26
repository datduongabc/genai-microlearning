"""Sinh module như 1 job step (job_queue.py) thay vì 1 HTTP request đồng bộ,
chặn -- đã thấy thật: 1 lần sinh mất 10+ phút (17 lệnh gọi LLM tuần tự),
trước đây không có cách nào retry tự động khi gặp RateLimitError giữa
chừng ngoài bấm lại tay (và bấm lại tay từng gây duplicate module trước khi
có lock/queue). Toàn bộ logic sinh giữ NGUYÊN như trước (chuyển từ
app/api/modules.py, không đổi hành vi) -- chỉ đổi cách được gọi và bỏ
`_generating_document_ids` in-memory lock (bảng jobs giờ là nguồn sự thật
duy nhất cho "có đang sinh dở không", không cần 2 cơ chế song song)."""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db import WorkerSessionLocal
from app.generation.content import generate_content
from app.generation.example import generate_example
from app.generation.objective import generate_learning_objective
from app.generation.planner import PLANNER_MAX_INPUT_CHARS, Concept, PlannerOutput, run_planner
from app.generation.quiz_graph import (
    LENGTH_TELL_FEEDBACK,
    generate_quiz_question,
    question_to_rebalance,
)
from app.generation.relevance import corrective_retrieve
from app.generation.scope_digest import build_scope_digest
from app.generation.slides import generate_slides
from app.llm.gateway import using_model
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


# Một bài 5-10 phút: mỗi câu quiz kiểm một sub-objective, tối đa ba câu.
MAX_QUIZ_QUESTIONS = 3


@dataclass
class _Asked:
    """Một câu hỏi đã sinh: thuộc quiz hay practice, nó kiểm điều gì, và kết quả sinh câu đó."""

    type: ComponentType
    target: str
    state: dict


def _generate_questions(
    db: Session, doc: Document, lo, practice_query: str, scope, self_check: bool = True
) -> list[_Asked]:
    """Quiz: một câu cho mỗi sub-objective (tối đa MAX_QUIZ_QUESTIONS), để cả bộ phủ đủ các phần
    của bài thay vì hỏi lại một ý. Practice: một câu luyện tập theo khái niệm chính."""
    asked = [
        _Asked(ComponentType.quiz, target, generate_quiz_question(
            db, doc.course_id, target, lo, "quiz", scope, self_check=self_check,
        ))
        for target in lo.sub_objectives[:MAX_QUIZ_QUESTIONS]
    ]
    asked.append(_Asked(
        ComponentType.practice, practice_query,
        generate_quiz_question(db, doc.course_id, practice_query, lo, "practice", scope, self_check=self_check),
    ))

    made = [a for a in asked if a.state.get("question") is not None]
    index = question_to_rebalance([a.state["question"] for a in made])
    if index is not None:
        stale = made[index]
        redone = generate_quiz_question(
            db, doc.course_id, stale.target, lo, stale.type.value, scope, feedback=LENGTH_TELL_FEEDBACK,
            self_check=self_check,
        )
        # Lần sinh lại không ra câu nào thì giữ câu cũ: một bộ lộ đáp án qua độ dài vẫn hơn một bộ thiếu câu.
        if redone.get("question") is not None:
            stale.state = redone
    return asked


def _save_questions(db: Session, module_id: uuid.UUID, asked: list[_Asked], self_check: bool = True) -> None:
    """Mỗi loại (quiz, practice) một thành phần, mỗi câu một Question theo đúng thứ tự sinh. Kết quả
    tự kiểm của từng câu chỉ vào bảng evaluations khi model thực sự đã kiểm câu đó."""
    for component_type in (ComponentType.quiz, ComponentType.practice):
        mine = [a for a in asked if a.type == component_type]
        component = ModuleComponent(
            module_id=module_id,
            type=component_type,
            content={"self_checked": self_check, "questions": [
                {
                    "target": a.target,
                    "passed": a.state.get("passed", False),
                    "correction_retry_count": a.state.get("correction_retry_count", 0),
                    "fail_reason": a.state.get("fail_reason"),
                    # Written before the answer was chosen: kept so a teacher can read why this is
                    # the answer, and so the answer can be audited against it.
                    "reasoning": a.state["question"].reasoning if a.state.get("question") is not None else None,
                }
                for a in mine
            ]},
        )
        db.add(component)
        db.flush()
        for position, a in enumerate(mine):
            if self_check:
                db.add(Evaluation(
                    module_id=module_id,
                    criterion=f"{component_type.value}_{position + 1}_groundedness_answerability_pass",
                    score=1.0 if a.state.get("passed") else 0.0,
                ))
            question = a.state.get("question")
            if question is not None:
                db.add(QuestionModel(
                    component_id=component.id,
                    position=position,
                    stem=question.stem,
                    choices={"options": question.choices},
                    answer_key=str(question.correct_index),
                    explanation=question.explanation,
                ))


def run_module_generation(
    db: Session, doc: Document, section: DocumentSection | None = None, self_check: bool = True
) -> Module:
    """Planner -> LO -> Content -> Slides -> Example -> Quiz/Practice ->
    Explanation. Nguyên logic chuyển từ _run_generate_module() cũ
    (app/api/modules.py) -- chỉ đổi return type (Module object, không phải
    dict HTTP response, vì caller giờ là job executor).

    Với `section` (1 chương/mục của giáo trình), mọi bước đều chỉ thấy các
    chunk của mục đó thay vì cả tài liệu.

    Chỉ Content và từng câu Quiz/Practice có vòng model tự kiểm (sinh lại một lần khi trượt); LO,
    Slides và Example sinh một lượt, ràng buộc còn lại nằm ở schema. `self_check=False` tắt cả hai
    vòng đó, để thí nghiệm so sánh module có và không có tự kiểm (bẫy cú pháp và luật cân bằng độ
    dài đáp án vẫn chạy vì không tốn lượt gọi model nào để phát hiện)."""
    planner_out = _get_or_cache_planner(db, doc, section)
    # A section confines every later step to its own chunks: retrieval, the
    # parent-context window and the quiz all see nothing outside it.
    scope = (
        ChunkScope(doc.id, section.first_chunk_index, section.last_chunk_index) if section is not None else None
    )
    top_concept = min(planner_out.concepts, key=lambda c: c.priority)
    query = f"{top_concept.name}: {top_concept.summary}"
    context_chunks = retrieve(db, doc.course_id, query, top_k=None, min_k=8, scope=scope)

    lo = generate_learning_objective(top_concept, context_chunks)

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
    content_out, content_check = generate_content(lo, content_context, self_check=self_check)
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
    if content_check is not None:
        _log_hallucination_check(db, module.id, "content_groundedness_pass", content_check.groundedness_pass)

    slides_out = generate_slides(lo, content_out.content)
    db.add(ModuleComponent(
        module_id=module.id, type=ComponentType.slides,
        content={"slides": [s.model_dump() for s in slides_out.slides]},
    ))

    example_out = generate_example(lo, content_out.content, context_chunks)
    db.add(ModuleComponent(
        module_id=module.id, type=ComponentType.example,
        content={"examples": [e.model_dump() for e in example_out.examples]},
    ))

    asked = _generate_questions(db, doc, lo, query, scope, self_check)

    # The Explanation component is what each question already wrote along with its answer: its
    # reasoning, written before the choices, and the explanation drawn from it, both checked along
    # with the question against its own passages. A separate step would only cost more model calls.
    items = []
    positions = {ComponentType.quiz: 0, ComponentType.practice: 0}
    for a in asked:
        position = positions[a.type]
        positions[a.type] += 1
        question = a.state.get("question")
        if question is None:
            continue
        items.append({
            "component": a.type.value, "position": position,
            "reasoning": question.reasoning, "explanation_text": question.explanation,
        })
    _save_questions(db, module.id, asked, self_check)
    if items:
        db.add(ModuleComponent(module_id=module.id, type=ComponentType.explanation, content={"items": items}))

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

        # Model giảng viên đã chọn nằm trong payload, vì request đã trả về từ lâu trước
        # khi worker bắt đầu. Bọc cả lượt sinh một lần ở đây thay vì truyền alias xuống
        # từng hàm: một lượt đi qua mười chỗ gọi LLM rải khắp generation và retrieval.
        # Thiếu khóa (job cũ, hoặc request không nói gì) nghĩa là bật tự kiểm như trước đây.
        self_check = current_job.payload.get("self_check", True)
        with using_model(current_job.payload.get("model_alias")):
            module = run_module_generation(db, doc, section, self_check=self_check)

        current_job.module_id = module.id
        current_job.status = JobStatus.done
        db.commit()
    finally:
        db.close()
