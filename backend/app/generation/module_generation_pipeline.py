"""Sinh module như 1 job step (job_queue.py) thay vì 1 HTTP request đồng bộ,
chặn -- đã thấy thật: 1 lần sinh mất 10+ phút (17 lệnh gọi LLM tuần tự),
trước đây không có cách nào retry tự động khi gặp RateLimitError giữa
chừng ngoài bấm lại tay (và bấm lại tay từng gây duplicate module trước khi
có lock/queue). Toàn bộ logic sinh giữ NGUYÊN như trước (chuyển từ
app/api/modules.py, không đổi hành vi) -- chỉ đổi cách được gọi và bỏ
`_generating_document_ids` in-memory lock (bảng jobs giờ là nguồn sự thật
duy nhất cho "có đang sinh dở không", không cần 2 cơ chế song song)."""

import contextvars
import logging
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from sqlalchemy import literal, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app import timing
from app.config import settings
from app.db import WorkerSessionLocal
from app.generation.checkpoint import Checkpoint
from app.generation.common import OutputLanguage
from app.generation.content import (
    ContentVerification,
    LessonText,
    LessonVerification,
    SourcedContent,
    generate_content,
)
from app.generation.example import ExampleOutput, generate_example
from app.generation.inline_citations import MARKER
from app.generation.objective import LearningObjective, generate_learning_objective
from app.generation.planner import Concept, PlannerOutput, run_planner
from app.generation.quiz_graph import (
    LENGTH_TELL_FEEDBACK,
    QuizQuestion,
    generate_quiz_question,
    question_to_rebalance,
    shuffled_choices,
)
from app.generation.relevance import CorrectiveResult, corrective_retrieve
from app.generation.slides import SlidesOutput, generate_slides
from app.jobs.queue import hold
from app.llm import catalog
from app.llm.gateway import capture_models, recorded_models, requested_model, using_model
from app.models import (
    Chunk,
    ComponentType,
    Document,
    Evaluation,
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
)
from app.models import Question as QuestionModel
from app.retrieval.search import ChunkScope, expand_with_parent_context
from app.retrieval.search import retrieve_for_generation as retrieve

logger = logging.getLogger(__name__)


def _get_or_cache_planner(
    db: Session, doc: Document,
    output_language: OutputLanguage = "vi", custom_instructions: str = "",
) -> PlannerOutput:
    """run_planner() tu nhan thiet ke "1 lenh goi/tai lieu" nhung truoc day
    bi goi lai MOI LAN generate duoc goi cho cung document -- nguyen nhan
    that gay dao dong bloom_level giua cac lan sinh (xac nhan qua bai test
    lap lai 18 tai lieu that: 3/17 doi ket qua). Cache 1 lan, tai su dung
    moi lan sau.
    """
    if (
        doc.planner_concepts
        and doc.planner_concepts[0].get("output_language") == output_language
        and doc.planner_concepts[0].get("custom_instructions", "") == custom_instructions
    ):
        return PlannerOutput(concepts=[Concept(**c) for c in doc.planner_concepts])

    # Do not retain the source reads while waiting on the planner.
    db.commit()
    planner_out = run_planner(doc.raw_text, output_language=output_language, custom_instructions=custom_instructions)
    doc.planner_concepts = [
        {**c.model_dump(), "output_language": output_language, "custom_instructions": custom_instructions.strip()} for c in planner_out.concepts
    ]
    db.commit()
    return planner_out


def _log_hallucination_check(db: Session, module_id: uuid.UUID, criterion: str, passed: bool) -> None:
    db.add(Evaluation(module_id=module_id, criterion=criterion, score=1.0 if passed else 0.0))


class IncompleteModuleError(ValueError):
    """Module thiếu một phần mà bước sau bắt buộc phải có, ví dụ chưa có Learning Objective."""


def learning_objective_of(module: Module) -> LearningObjective:
    """Learning Objective đã lưu của một module, để nhánh không RAG sinh trên đúng LO của module
    RAG đi cặp thay vì tự đặt LO mới."""
    if not module.learning_objective or not module.bloom_level or not module.sub_objectives:
        raise IncompleteModuleError("Module chưa có đủ Learning Objective, mức Bloom và sub-objective.")
    # verb chỉ dùng khi sinh Learning Objective; ở đây Learning Objective đã có sẵn.
    return LearningObjective(
        bloom_level=module.bloom_level, verb="", objective_text=module.learning_objective,
        sub_objectives=list(module.sub_objectives),
    )


def concept_query_of(module: Module) -> str:
    """Câu truy vấn pipeline RAG đã dùng cho module: "tên khái niệm: tóm tắt" của Planner. Không tìm
    lại được khái niệm (Planner đã chạy lại, đổi tên) thì dùng tên module cùng Learning Objective."""
    for concept in module.document.planner_concepts or []:
        if concept.get("name") == module.title:
            return f"{concept['name']}: {concept['summary']}"
    return f"{module.title}: {module.learning_objective or ''}"


GENERATION_DEFAULTS = {
    "self_check": True, "output_language": "vi", "custom_instructions": "", "use_retrieval": True,
}


def normalize_generation_payload(payload: dict) -> dict:
    return {**GENERATION_DEFAULTS, **payload}


def generation_payload_of(db: Session, module_id: uuid.UUID) -> dict:
    """Stored generation conditions, with a job fallback for legacy modules."""
    module = db.get(Module, module_id)
    if module is not None and module.generation_payload is not None:
        return normalize_generation_payload(module.generation_payload)
    job = (
        db.query(Job)
        .filter(Job.module_id == module_id, Job.job_type == JobType.module_generation)
        .order_by(Job.created_at.desc())
        .first()
    )
    return normalize_generation_payload((job.payload or {}) if job is not None else {})


def generation_payload(
    alias: str | None, self_check: bool = True, output_language: OutputLanguage = "vi", custom_instructions: str = "",
) -> dict:
    """Không ghi alias khi chưa chọn, thay vì ghi sẵn giá trị từ cấu hình: ghi sẵn thì
    đổi cấu hình hệ thống sẽ không tác dụng với các job đã nằm trong hàng đợi. `self_check`
    luôn được ghi, để biết chắc mỗi module của thí nghiệm thuộc nhánh nào."""
    payload: dict = {"self_check": self_check, "output_language": output_language}
    if custom_instructions.strip():
        payload["custom_instructions"] = custom_instructions.strip()
    if alias:
        payload["model_alias"] = alias
    return payload


def control_payload(
    paired_module_id: uuid.UUID, alias: str | None, self_check: bool,
    output_language: OutputLanguage = "vi", custom_instructions: str = "",
) -> dict:
    """Payload của job sinh bản đối chứng không RAG, như nhau dù job đến từ API hay từ CLI."""
    return {
        **generation_payload(alias, self_check, output_language, custom_instructions),
        "use_retrieval": False, "paired_module_id": str(paired_module_id),
    }


def matching_control(db: Session, module_id: uuid.UUID, payload: dict) -> Module | None:
    payload = normalize_generation_payload(payload)
    defaults = literal(GENERATION_DEFAULTS, type_=JSONB)
    control = db.scalar(select(Module).where(
        Module.paired_module_id == module_id,
        defaults.op("||")(Module.generation_payload) == payload,
    ).order_by(Module.created_at.desc(), Module.id))
    if control is not None:
        return control
    job = db.scalar(select(Job).outerjoin(Module, Job.module_id == Module.id).where(
        Module.generation_payload.is_(None), Job.module_id.is_not(None),
        Job.job_type == JobType.module_generation, Job.status == JobStatus.done,
        defaults.op("||")(Job.payload) == payload,
    ).order_by(Job.created_at.desc(), Job.id))
    if job is None:
        return None
    control = db.get(Module, job.module_id)
    if control is None:
        raise LookupError("Module not found")
    return control


def is_control(db: Session, module_id: uuid.UUID) -> bool:
    """Nhận diện bản đối chứng theo điều kiện lưu trên module, dự phòng bằng job cho module cũ."""
    return generation_payload_of(db, module_id).get("use_retrieval", True) is False


# Một bài 5-10 phút: mỗi câu quiz kiểm một sub-objective, tối đa ba câu.
MAX_QUIZ_QUESTIONS = 3
# Rewrites of one question whose key is the longest option, after which the set is accepted as it is:
# each rewrite is a paid generation and review.
MAX_REBALANCES = 2


@dataclass
class _Asked:
    """Một câu hỏi đã sinh: thuộc quiz hay practice, nó kiểm điều gì, và kết quả sinh câu đó."""

    type: ComponentType
    target: str
    state: dict


def _question_key(component_type: ComponentType, target: str) -> str:
    return f"question:{component_type.value}:{target}"


def _state_out(state: dict) -> dict:
    question = state.get("question")
    return {**state, "question": question.model_dump() if question is not None else None}


def _state_in(saved: dict) -> dict:
    question = saved.get("question")
    return {**saved, "question": QuizQuestion.model_validate(question) if question is not None else None}


def _generate_questions(
    db: Session, doc: Document, lo, practice_query: str, scope, self_check: bool = True, lesson: str | None = None,
    output_language: OutputLanguage = "vi", custom_instructions: str = "", checkpoint: Checkpoint | None = None,
) -> list[_Asked]:
    """Quiz: một câu cho mỗi sub-objective (tối đa MAX_QUIZ_QUESTIONS), để cả bộ phủ đủ các phần
    của bài thay vì hỏi lại một ý. Practice: một câu luyện tập theo khái niệm chính. `lesson`
    (nhánh không RAG): mọi câu đọc bài học này thay cho đoạn truy hồi."""
    wanted = [(ComponentType.quiz, target) for target in lo.sub_objectives[:MAX_QUIZ_QUESTIONS]]
    wanted.append((ComponentType.practice, practice_query))

    def ask(component_type: ComponentType, target: str) -> _Asked:
        # Retrieval opens its own session from db's bind, so the threads never share this session.
        return _Asked(component_type, target, generate_quiz_question(
            db, doc.course_id, target, lo, component_type.value, scope, self_check=self_check, lesson=lesson,
            output_language=output_language, custom_instructions=custom_instructions,
        ))

    # A question an earlier run of the job already wrote is not asked again.
    saved = {item: checkpoint.get(_question_key(*item)) if checkpoint is not None else None for item in wanted}
    # One context per thread carries the chosen model, the experiment pin and the timing spans.
    with ThreadPoolExecutor(max_workers=settings.question_concurrency) as pool:
        futures = {item: pool.submit(contextvars.copy_context().run, ask, *item)
                   for item in wanted if saved[item] is None}
    # Written once every call has returned, so that no transaction is open during one, and before a
    # failed question is raised, so that the questions that were made are not paid for again.
    if checkpoint is not None:
        for item, future in futures.items():
            if future.exception() is None:
                checkpoint.put(_question_key(*item), _state_out(future.result().state))
    asked = [futures[item].result() if item in futures else _Asked(item[0], item[1], _state_in(saved[item]))
             for item in wanted]

    made = [a for a in asked if a.state.get("question") is not None]
    for _ in range(MAX_REBALANCES):
        index = question_to_rebalance([a.state["question"] for a in made])
        if index is None:
            break
        stale = made[index]
        redone = generate_quiz_question(
            db, doc.course_id, stale.target, lo, stale.type.value, scope, feedback=LENGTH_TELL_FEEDBACK,
            self_check=self_check, lesson=lesson,
            output_language=output_language, custom_instructions=custom_instructions,
        )
        # Keep the evidence even if a rebalance cannot produce a replacement.
        if redone.get("question") is not None:
            stale.state = redone
    if (index := question_to_rebalance([a.state["question"] for a in made])) is not None:
        stale = made[index]
        stale.state = {**stale.state, "passed": False, "fail_reason": "; ".join(
            r for r in (stale.state.get("fail_reason"), LENGTH_TELL_FEEDBACK) if r
        )}
    for a in made:
        a.state = {**a.state, "question": shuffled_choices(a.state["question"])}
    return asked


def _save_questions(
    db: Session, module_id: uuid.UUID, asked: list[_Asked], self_check: bool = True, use_retrieval: bool = True
) -> None:
    """Mỗi loại (quiz, practice) một thành phần, mỗi câu một Question theo đúng thứ tự sinh. Kết quả
    tự kiểm của từng câu chỉ vào bảng evaluations khi model thực sự đã kiểm câu đó; nhánh không RAG
    không chấm groundedness nên tiêu chí mang tên khác."""
    checked = "groundedness_answerability_pass" if use_retrieval else "answerability_pass"
    if self_check:
        # Question writing is the weakest step (the report's rubric study): its own checks, counted
        # over every module made, show it getting worse before a lecturer has to say so.
        logger.info("ai_quality module_questions", extra={
            "questions": len(asked), "failed_check": sum(1 for a in asked if not a.state.get("passed", False)),
        })
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
                    criterion=f"{component_type.value}_{position + 1}_{checked}",
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


def _retrieve_for_content(
    db: Session, doc: Document, lo: LearningObjective, context_chunks: list[Chunk], scope: ChunkScope | None
) -> CorrectiveResult:
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
    return corrective_retrieve(
        objective_text,
        context_chunks + extra_chunks,
        lambda refined: retrieve(db, doc.course_id, refined, top_k=None, min_k=8, scope=scope),
    )


@capture_models()
def run_module_generation(
    db: Session, doc: Document,
    self_check: bool = True, use_retrieval: bool = True, paired_module: Module | None = None,
    output_language: OutputLanguage = "vi", custom_instructions: str = "",
    before_save: Callable[[Session, Module], None] | None = None,
    checkpoint: Checkpoint | None = None,
) -> Module:
    """`checkpoint` holds the steps an earlier run of the same job finished (app/generation/checkpoint.py).
    `before_save` runs once the module has its id, in the transaction that writes it: the job queue
    checks there that its worker still holds the job and marks it done (app/jobs/queue.py, hold)."""
    # Keep read values in memory after each commit; expired attributes would silently reopen
    # a transaction when a prompt reads them. Restore the caller's session setting afterwards.
    expire_on_commit = db.expire_on_commit
    db.expire_on_commit = False
    try:
        with timing.capture_timings() as spans:
            return _generate_module(db, doc, self_check, use_retrieval, paired_module,
                                    output_language, custom_instructions, spans, before_save, checkpoint)
    finally:
        db.expire_on_commit = expire_on_commit


@dataclass
class _Objective:
    """What every later step builds on: the module's title, the search query its questions use,
    its Learning Objective and, on the RAG branch, the passages the objective was written from."""

    title: str
    query: str
    lo: LearningObjective
    context_chunks: list[Chunk] | None


def _objective_from_sources(
    db: Session, doc: Document, scope: ChunkScope,
    output_language: OutputLanguage, custom_instructions: str,
) -> _Objective:
    with timing.stage("planner"):
        planner_out = _get_or_cache_planner(db, doc, output_language, custom_instructions)
    top_concept = min(planner_out.concepts, key=lambda c: c.priority)
    query = f"{top_concept.name}: {top_concept.summary}"
    db.commit()
    with timing.stage("retrieval"):
        context_chunks = retrieve(db, doc.course_id, query, top_k=None, min_k=8, scope=scope)
    db.commit()
    with timing.stage("learning_objective"):
        lo = generate_learning_objective(
            top_concept, context_chunks, output_language=output_language, custom_instructions=custom_instructions,
        )
    return _Objective(top_concept.name, query, lo, context_chunks)


def _objective_from_pair(doc: Document, paired_module: Module | None) -> _Objective:
    if paired_module is None:
        raise ValueError("Sinh không RAG cần module RAG đi cặp để lấy Learning Objective.")
    if paired_module.document_id != doc.id:
        raise ValueError("Module đi cặp không thuộc đúng tài liệu đang sinh.")
    return _Objective(
        paired_module.title, concept_query_of(paired_module), learning_objective_of(paired_module), None,
    )


def _new_module(
    doc: Document, objective: _Objective, paired_module: Module | None, self_check: bool,
    use_retrieval: bool, output_language: OutputLanguage, custom_instructions: str,
) -> Module:
    if use_retrieval:
        payload = {**generation_payload(requested_model(), self_check, output_language, custom_instructions),
                   "use_retrieval": True}
    else:
        payload = control_payload(paired_module.id, requested_model(), self_check, output_language, custom_instructions)
    return Module(
        document_id=doc.id,
        course_id=doc.course_id,
        title=objective.title,
        paired_module_id=paired_module.id if paired_module is not None else None,
        generation_payload=normalize_generation_payload(payload),
        learning_objective=objective.lo.objective_text,
        bloom_level=objective.lo.bloom_level,
        sub_objectives=objective.lo.sub_objectives,
    )


def _content_component(module: Module, content_out, corrected: CorrectiveResult | None) -> ModuleComponent:
    # Nhánh không RAG giữ nguyên hình dạng thành phần Content: không có nguồn nên không có trích dẫn,
    # và không chấm độ liên quan (None).
    return ModuleComponent(
        module_id=module.id, type=ComponentType.content,
        content={
            "markdown": content_out.content,
            "source_locators": getattr(content_out, "source_locators", []),
            **({"source_map": {k: v.model_dump() for k, v in content_out.source_map.items()},
                "inline_citations": [c.model_dump() for c in content_out.inline_citations]}
               if hasattr(content_out, "source_map") else {"claim_citations": []}),
            "relevance_confidence": corrected.confidence if corrected is not None else None,
            "relevance_retry_count": corrected.retries if corrected is not None else 0,
        },
    )


def _explanation_items(asked: list[_Asked]) -> list[dict]:
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
    return items


def _step(checkpoint: Checkpoint | None, name: str, model, make):
    """One step that returns a pydantic model: read back if an earlier run did it, else made and written."""
    saved = checkpoint.get(name) if checkpoint is not None else None
    if saved is not None:
        return model.model_validate(saved)
    with timing.stage(name):
        made = make()
    if checkpoint is not None:
        checkpoint.put(name, made.model_dump())
    return made


def _objective_out(objective: _Objective) -> dict:
    return {"title": objective.title, "query": objective.query, "lo": objective.lo.model_dump(),
            "context_chunk_ids": [str(c.id) for c in objective.context_chunks]
            if objective.context_chunks is not None else None}


def _objective_in(db: Session, doc: Document, saved: dict) -> _Objective:
    ids = saved["context_chunk_ids"]
    chunks = None
    if ids is not None:
        found = {str(c.id): c for c in db.query(Chunk).filter(
            Chunk.document_id == doc.id, Chunk.id.in_([uuid.UUID(i) for i in ids]))}
        # A chunk gone since (the document was embedded again) is left out, as a search would.
        chunks = [found[i] for i in ids if i in found]
    return _Objective(saved["title"], saved["query"], LearningObjective.model_validate(saved["lo"]), chunks)


def _content_out(content_out, content_check, corrected: CorrectiveResult | None) -> dict:
    return {"content": content_out.model_dump(),
            "check": content_check.model_dump() if content_check is not None else None,
            # Only what the module keeps of the relevance check: the passages were used for the content.
            "corrected": {"confidence": corrected.confidence, "retries": corrected.retries}
            if corrected is not None else None}


def _content_in(saved: dict, use_retrieval: bool):
    content_model, check_model = (SourcedContent, ContentVerification) if use_retrieval else (LessonText, LessonVerification)
    check = check_model.model_validate(saved["check"]) if saved["check"] is not None else None
    corrected = CorrectiveResult([], **saved["corrected"]) if saved["corrected"] is not None else None
    return content_model.model_validate(saved["content"]), check, corrected


def _generate_module(
    db: Session, doc: Document, self_check: bool = True,
    use_retrieval: bool = True, paired_module: Module | None = None,
    output_language: OutputLanguage = "vi", custom_instructions: str = "",
    spans: list[dict] | None = None, before_save: Callable[[Session, Module], None] | None = None,
    checkpoint: Checkpoint | None = None,
) -> Module:
    """Planner -> LO -> Content -> Slides -> Example -> Quiz/Practice ->
    Explanation. Nguyên logic chuyển từ _run_generate_module() cũ
    (app/api/modules.py) -- chỉ đổi return type (Module object, không phải
    dict HTTP response, vì caller giờ là job executor).

    Chỉ Content và từng câu Quiz/Practice có vòng model tự kiểm (sinh lại một lần khi trượt); LO,
    Slides và Example sinh một lượt, ràng buộc còn lại nằm ở schema. `self_check=False` tắt cả hai
    vòng đó, để thí nghiệm so sánh module có và không có tự kiểm (bẫy cú pháp và luật cân bằng độ
    dài đáp án vẫn chạy vì không tốn lượt gọi model nào để phát hiện).

    `use_retrieval=False` là nhóm đối chứng llm_no_rag của nghiên cứu RAG và không RAG: cùng chuỗi
    bước, cùng cấu trúc module, nhưng không truy hồi gì. Learning Objective lấy từ `paired_module`
    (module RAG đi cặp) thay vì Planner và LO tự đặt lại; mỗi bước dùng đúng prompt của nhánh RAG
    trừ khối SOURCE CHUNK, và câu hỏi (kèm lời giải) đọc Content thay cho đoạn truy hồi. Các bước
    tự kiểm bỏ những tiêu chí cần nguồn để đối chiếu (groundedness)."""
    started = time.perf_counter()
    # Every source must belong to the module's document, where citations resolve.
    scope = ChunkScope(doc.id)
    language = {"output_language": output_language, "custom_instructions": custom_instructions}

    saved = checkpoint.get("objective") if checkpoint is not None else None
    if saved is not None:
        objective = _objective_in(db, doc, saved)
    elif use_retrieval:
        objective = _objective_from_sources(db, doc, scope, output_language, custom_instructions)
    else:
        objective = _objective_from_pair(doc, paired_module)
    if checkpoint is not None and saved is None:
        checkpoint.put("objective", _objective_out(objective))
    lo = objective.lo
    db.commit()
    module = _new_module(doc, objective, paired_module, self_check, use_retrieval, output_language, custom_instructions)

    saved = checkpoint.get("content") if checkpoint is not None else None
    if saved is not None:
        content_out, content_check, corrected = _content_in(saved, use_retrieval)
    else:
        corrected = content_context = None
        if use_retrieval:
            with timing.stage("content_retrieval"):
                corrected = _retrieve_for_content(db, doc, lo, objective.context_chunks, scope)
                content_context = expand_with_parent_context(db, corrected.chunks, scope=scope)
            db.commit()
        with timing.stage("content"):
            content_out, content_check = generate_content(lo, content_context, self_check=self_check, **language)
        if checkpoint is not None:
            checkpoint.put("content", _content_out(content_out, content_check, corrected))
    content_component = _content_component(module, content_out, corrected)

    lesson_text = MARKER.sub("", content_out.content)
    slides_out = _step(checkpoint, "slides", SlidesOutput, lambda: generate_slides(lo, lesson_text, **language))
    slides_component = ModuleComponent(
        module_id=module.id, type=ComponentType.slides,
        content={"slides": [s.model_dump() for s in slides_out.slides]},
    )

    example_out = _step(checkpoint, "example", ExampleOutput,
                        lambda: generate_example(lo, lesson_text, objective.context_chunks, **language))
    example_component = ModuleComponent(
        module_id=module.id, type=ComponentType.example,
        content={"examples": [e.model_dump() for e in example_out.examples]},
    )

    with timing.stage("questions"):
        asked = _generate_questions(
            db, doc, lo, objective.query, scope, self_check,
            lesson=None if use_retrieval else lesson_text, **language, checkpoint=checkpoint,
        )
    items = _explanation_items(asked)

    # Persist the complete result atomically, after every provider call has finished.
    db.add(module)
    db.flush()
    if before_save is not None:
        before_save(db, module)
    for component in (content_component, slides_component, example_component):
        component.module_id = module.id
        db.add(component)
    if corrected is not None:
        _log_hallucination_check(db, module.id, "content_relevance_pass", corrected.confidence == "high")
    if content_check is not None and use_retrieval:
        _log_hallucination_check(db, module.id, "content_groundedness_pass", content_check.groundedness_pass)
    _save_questions(db, module.id, asked, self_check, use_retrieval)
    if items:
        db.add(ModuleComponent(module_id=module.id, type=ComponentType.explanation, content={"items": items}))

    module.generation_models = recorded_models()
    if spans is not None:
        module.generation_timings = {
            "total_seconds": round(time.perf_counter() - started, 4),
            "summary": timing.summarize(spans), "spans": list(spans),
        }
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
            raise ValueError("Tài liệu chưa xử lý xong.")

        # Model giảng viên đã chọn nằm trong payload, vì request đã trả về từ lâu trước
        # khi worker bắt đầu. Bọc cả lượt sinh một lần ở đây thay vì truyền alias xuống
        # từng hàm: một lượt đi qua mười chỗ gọi LLM rải khắp generation và retrieval.
        # Thiếu khóa (job cũ, hoặc request không nói gì) nghĩa là bật tự kiểm như trước đây.
        self_check = current_job.payload.get("self_check", True)
        # Nhóm đối chứng không RAG: job ghi module RAG đi cặp, Learning Objective lấy từ đó.
        use_retrieval = current_job.payload.get("use_retrieval", True)
        paired_module = None
        if not use_retrieval:
            paired_id = current_job.payload.get("paired_module_id")
            paired_module = db.get(Module, uuid.UUID(paired_id)) if paired_id else None
            if paired_module is None:
                raise ValueError(f"Paired module {paired_id} not found")
        alias = current_job.payload.get("model_alias")
        # A no-RAG control keeps the alias of its RAG pair even when it is gone: on another model the pair
        # would no longer differ only in retrieval.
        if not use_retrieval and alias and catalog.served_or_default(alias) != alias:
            # The gateway's 400 is not a ValueError, so the queue would retry it five times for nothing.
            raise ValueError(f"Model '{alias}' of the pair is no longer offered")
        with using_model(catalog.served_or_default(alias) if use_retrieval else alias):
            run_module_generation(
                db, doc, self_check=self_check, use_retrieval=use_retrieval, paired_module=paired_module,
                output_language=current_job.payload.get("output_language", "vi"),
                custom_instructions=current_job.payload.get("custom_instructions", ""),
                before_save=lambda session, module: _finish(hold(session, job), module),
                checkpoint=Checkpoint(db, current_job.id),
            )
    finally:
        db.close()


def _finish(job: Job, module: Module) -> None:
    job.module_id = module.id
    job.status = JobStatus.done
