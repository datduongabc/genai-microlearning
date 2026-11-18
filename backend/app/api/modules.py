import uuid
from datetime import UTC, datetime
from io import BytesIO
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pptx import Presentation
from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.chat import ChatRequest, _get_or_create_session
from app.api.deletion import check_study_references, commit_deletion
from app.api.deps import get_current_user
from app.api.model_alias import usable_alias as _usable_alias
from app.auth.rate_limit import limiter, user_or_address
from app.chat.service import HISTORY_TURNS, QuizQuestionEdit, module_edit_count, propose_module_edit
from app.citations import citation_label
from app.db import get_db
from app.export import scorm
from app.export.lesson import build_book_chapters, failing_questions
from app.generation.common import OutputLanguage
from app.generation.content import ContentOutput, resolve_citations
from app.generation.example import ExampleOutput
from app.generation.inline_citations import MARKER, citation_content
from app.generation.limits import has_full_access, reserve_generations
from app.generation.module_generation_pipeline import (
    IncompleteModuleError,
    control_payload,
    generation_payload,
    generation_payload_of,
    is_control,
    learning_objective_of,
)
from app.generation.slides import SlidesOutput
from app.generation.study_packets import reference_scope_chunks, resolved_citations, small_reference
from app.jobs.queue import enqueue_job
from app.llm import catalog
from app.llm.gateway import using_model
from app.models import (
    ChatMessage,
    ChatRole,
    ComponentType,
    Document,
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
    Question,
    User,
)
from app.retrieval.search import ChunkScope, retrieve

router = APIRouter(prefix="/modules", tags=["modules"], dependencies=[Depends(get_current_user)])


class GenerateRequest(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _reject_section_ids(cls, data):
        if isinstance(data, dict) and "section_ids" in data:
            raise ValueError("Sinh mô-đun theo mục đã bị gỡ bỏ: không còn nhận section_ids, mô-đun luôn sinh cho cả tài liệu.")
        return data

    # Model cho riêng lượt sinh này, ghi đè mặc định của giảng viên. Bỏ trống thì dùng
    # mặc định của họ, không có mặc định thì dùng cấu hình hệ thống.
    model_alias: str | None = None
    # Tắt vòng model tự kiểm Content và câu hỏi (thí nghiệm có/không tự kiểm cho chương đánh giá).
    self_check: bool = True
    output_language: OutputLanguage = "vi"
    custom_instructions: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""
    # False: sinh bản đối chứng không RAG (nhóm llm_no_rag) của module RAG `paired_module_id`, trên
    # đúng Learning Objective và tài liệu của nó. Model và cờ tự kiểm bỏ trống thì theo module RAG đó.
    use_retrieval: bool = True
    paired_module_id: uuid.UUID | None = None


def _selected_alias(body: GenerateRequest | None, current_user: User) -> str | None:
    """Alias sẽ dùng cho lượt sinh này, hoặc None nghĩa là để cấu hình hệ thống quyết định.

    Kiểm ở đây chứ không tin client: một alias lạ lọt xuống hàng đợi thì lượt sinh chạy
    mười phút rồi mới hỏng ở lệnh gọi LLM, và lỗi chẳng nói gì về việc chọn model.
    """
    alias = body.model_alias if body is not None else None
    if alias is not None:
        return _usable_alias(alias)
    alias = current_user.default_model_alias
    if alias is None:
        # Đường mặc định: không có gì để kiểm hay thử, nên không gọi gateway lúc bấm nút.
        return None
    if not catalog.is_available(alias):
        # Mặc định đã lưu có thể trỏ tới alias gateway đã bỏ (gemini-pro đổi thành
        # gemini-flash): lùi về cấu hình hệ thống, đừng chặn mọi lượt sinh bằng 422.
        return None
    return _usable_alias(alias)


def _owned_document(
    db: Session, document_id: uuid.UUID, current_user: User, *, lock: bool = False
) -> Document:
    doc = db.get(Document, document_id, with_for_update=lock)
    if doc is None or doc.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _owned_module(db: Session, module_id: uuid.UUID, current_user: User) -> Module:
    module = db.get(Module, module_id)
    if module is None or module.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Module not found")
    return module


def _generate_without_retrieval(
    db: Session, doc: Document, body: GenerateRequest, current_user: User, full_access: bool,
):
    """Một job sinh bản đối chứng không RAG (nhóm llm_no_rag) của module RAG `paired_module_id`: cùng
    Learning Objective, cùng tài liệu, và theo mặc định cùng model và cờ tự kiểm đã sinh module RAG đó,
    để khác biệt giữa hai bản chỉ còn là RAG."""
    if body.use_retrieval:
        raise HTTPException(status_code=422, detail="paired_module_id chỉ dùng khi use_retrieval=false.")
    if body.paired_module_id is None:
        raise HTTPException(
            status_code=422,
            detail="Sinh không RAG cần paired_module_id: module RAG đi cặp để lấy Learning Objective.",
        )
    paired = _owned_module(db, body.paired_module_id, current_user)
    if paired.document_id != doc.id:
        raise HTTPException(status_code=422, detail="Module đi cặp không thuộc tài liệu này.")
    try:
        learning_objective_of(paired)
    except IncompleteModuleError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if is_control(db, paired.id):
        raise HTTPException(status_code=422, detail="Module đi cặp phải là module sinh có RAG.")
    paired_payload = generation_payload_of(db, paired.id)
    alias = body.model_alias or paired_payload.get("model_alias")
    alias = _usable_alias(alias) if alias is not None else None
    self_check = body.self_check if "self_check" in body.model_fields_set else paired_payload.get("self_check", True)

    running = (
        db.query(Job.id)
        .filter(
            Job.job_type == JobType.module_generation, Job.document_id == doc.id,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
        .first()
    )
    if running is not None:
        raise HTTPException(status_code=409, detail="Tài liệu này đang tạo một mô-đun khác. Hãy chờ lượt đó xong.")

    reserve_generations(db, current_user, full_access=full_access)
    job = enqueue_job(
        db, job_type=JobType.module_generation, step="generate", document_id=doc.id,
        payload=control_payload(paired.id, alias, self_check, paired_payload.get("output_language", "vi"),
                                paired_payload.get("custom_instructions", "")),
    )
    db.commit()
    return {"job_id": job.id, "status": job.status}


@router.post("/generate")
@limiter.limit("10/minute", key_func=user_or_address)
def generate_module(
    request: Request, document_id: uuid.UUID, body: GenerateRequest | None = None,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    without_retrieval = body is not None and (not body.use_retrieval or body.paired_module_id is not None)
    # Network calls before the lock (Moodle, and the model probe), so the document row is not held
    # while they wait. The control arm reads its model from the paired module, so it probes later.
    full_access = has_full_access(db, current_user)
    alias = None if without_retrieval else _selected_alias(body, current_user)
    # FOR UPDATE: the "already generating" checks below read the jobs table, so
    # two requests for one document must not run them at the same moment or
    # both would pass and start the same work twice.
    doc = _owned_document(db, document_id, current_user, lock=True)
    if not doc.raw_text:
        raise HTTPException(status_code=422, detail="Tài liệu chưa xử lý xong.")

    if body is not None and without_retrieval:
        return _generate_without_retrieval(db, doc, body, current_user, full_access)

    self_check = body.self_check if body is not None else True

    output_language = body.output_language if body is not None else "vi"
    custom_instructions = body.custom_instructions if body is not None else ""
    # Bảng jobs giờ là nguồn sự thật DUY NHẤT cho "có đang sinh dở không" --
    # trước đây có 1 lock RIÊNG trong bộ nhớ (_generating_document_ids) cho
    # việc này; job queue tự nhiên thay thế nó, tránh 2 cơ chế song song có
    # thể lệch nhau. Chặn double-submit cùng document_id (double-click, đa
    # tab, hoặc UI mất state "đang sinh" sau khi remount page rồi bấm lại -
    # bug thật đã gặp và verify sống trong session này).
    existing = (
        db.query(Job)
        .filter(
            Job.job_type == JobType.module_generation, Job.document_id == document_id,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="Tài liệu này đang tạo một mô-đun khác. Hãy chờ lượt đó xong.",
        )

    # generate_module() thật đo được mất 10+ phút (17 lệnh gọi LLM tuần tự) --
    # chạy nền qua job queue thay vì giữ request HTTP chờ suốt, xem
    # app/services/module_generation_pipeline.py + job_queue.py.
    reserve_generations(db, current_user, full_access=full_access)
    job = enqueue_job(
        db, job_type=JobType.module_generation, step="generate", document_id=document_id,
        payload=generation_payload(alias, self_check, output_language, custom_instructions),
    )
    db.commit()
    return {"job_id": job.id, "status": job.status}


@router.get("/jobs/{job_id}")
def get_module_job_status(
    job_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    job = db.get(Job, job_id)
    if job is None or job.document_id is None:
        raise HTTPException(status_code=404, detail="Job not found")
    doc = db.get(Document, job.document_id)
    if doc is None or doc.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id, "step": job.step, "status": job.status,
        "attempts": job.attempts, "last_error": job.last_error,
        "module_id": job.module_id,
    }


@router.get("")
def list_modules(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    _owned_document(db, document_id, current_user)

    modules = (
        db.query(Module)
        .filter(Module.document_id == document_id)
        .order_by(Module.created_at.desc())
        .all()
    )
    return [
        {
            "id": m.id,
            "title": m.title,
            "learning_objective": m.learning_objective,
            "bloom_level": m.bloom_level,
            "component_count": len(m.components),
            "is_control": is_control(db, m.id),
        }
        for m in modules
    ]


_COMPONENT_ORDER = {
    ComponentType.content: 0,
    ComponentType.slides: 1,
    ComponentType.example: 2,
    ComponentType.quiz: 3,
    ComponentType.practice: 4,
    ComponentType.explanation: 5,
}


@router.get("/{module_id}")
def get_module(
    module_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    # Eager-loaded (self-audit found this was a real N+1: 1 module + 1
    # course + 1 components + 1-per-component questions query with the
    # default lazy='select' relationships -- 9 statements for a 6-component
    # module). joinedload for course (single row, needed only for the
    # ownership check); selectinload for the one-to-many chains, which
    # avoids the row-multiplication a joinedload would cause here.
    module = (
        db.query(Module)
        .options(
            joinedload(Module.course),
            selectinload(Module.components).selectinload(ModuleComponent.questions),
        )
        .filter(Module.id == module_id)
        .first()
    )
    if module is None or module.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Module not found")
    # Components created in generate_module() all commit in one transaction, so
    # Postgres' now() (transaction time, not statement time) ties their
    # created_at — DB order is otherwise undefined. Sort by the fixed
    # pedagogical reading order instead (content -> slides -> ... -> explanation).
    ordered_components = sorted(module.components, key=lambda c: _COMPONENT_ORDER.get(c.type, 99))
    control = is_control(db, module.id)
    citations = None if control else resolved_citations(db, module)
    if citations is not None:
        citations = [
            c.model_copy(update={"locator": citation_label(module.document.filename, c.locator)})
            for c in citations
        ]
    return {
        "id": module.id,
        "title": module.title,
        "learning_objective": module.learning_objective,
        "bloom_level": module.bloom_level,
        "sub_objectives": module.sub_objectives,
        "generation_models": module.generation_models,
        "course_id": module.course_id,
        "moodle_course_id": module.course.moodle_course_id,
        "moodle_published_at": module.moodle_published_at,
        "is_control": control,
        "claim_citations": citations,
        "chat_edit_count": module_edit_count(db, module.id),
        "components": [
            {
                "id": c.id,
                "type": c.type,
                "content": c.content,
                "questions": [
                    {"id": q.id, "position": q.position, "stem": q.stem, "choices": q.choices,
                     "answer_key": q.answer_key, "explanation": q.explanation}
                    for q in c.questions
                ],
            }
            for c in ordered_components
        ],
    }


@router.get("/{module_id}/slides.pptx")
def export_slides(
    module_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    module = _owned_module(db, module_id, current_user)
    component = next((c for c in module.components if c.type == ComponentType.slides), None)
    if component is None:
        raise HTTPException(status_code=404, detail="Slides not found")
    deck = Presentation()
    for entry in component.content["slides"]:
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = entry["title"]
        frame = slide.placeholders[1].text_frame
        for index, bullet in enumerate(b for b in entry["bullets"] if b):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.text = bullet
    output = BytesIO()
    deck.save(output)
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(module.title + '.pptx', safe='')}"},
    )


@router.get("/{module_id}/scorm.zip")
def export_scorm(
    module_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """The module as a SCORM 1.2 package for any LMS, under the same rules as publishing to Moodle."""
    module = _owned_module(db, module_id, current_user)
    if is_control(db, module.id):
        raise HTTPException(status_code=422, detail="Bản đối chứng chỉ dùng cho nghiên cứu nên không xuất ra ngoài được.")
    failing = failing_questions(module)
    if failing:
        raise HTTPException(
            status_code=422,
            detail="Còn câu hỏi chưa khớp nguồn. Hãy sửa trước khi xuất gói SCORM: " + "; ".join(failing),
        )
    if not build_book_chapters(module):
        raise HTTPException(status_code=422, detail="Mô-đun chưa có nội dung nào để xuất.")
    return Response(
        content=scorm.build_package(module),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(module.title + ' - SCORM.zip', safe='')}"},
    )


@router.patch("/{module_id}")
def update_module(
    module_id: uuid.UUID, title: str | None = Query(default=None, max_length=200), db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    module = _owned_module(db, module_id, current_user)
    if title is not None:
        module.title = title
    db.commit()
    return {"id": module.id, "title": module.title}


@router.delete("/{module_id}")
def delete_module(
    module_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Xóa một module đã sinh cùng bản đối chứng không RAG đi cặp với nó (nếu có).

    Components, câu hỏi, đánh giá, lịch sử chat-edit và job đã sinh module đều theo ON DELETE
    CASCADE. Bảng chỉ lưu thời điểm đẩy lên Moodle, không lưu id hoạt động, nên không có tham chiếu
    nào để dọn; Book/Quiz đã tạo trên Moodle giữ nguyên, giống như xóa khóa học.
    """
    module = _owned_module(db, module_id, current_user)
    # Cùng khóa với /modules/generate: một lượt sinh bản đối chứng không được chen vào giữa lúc kiểm và lúc xóa.
    _owned_document(db, module.document_id, current_user, lock=True)

    paired_jobs = db.query(Job).filter(
        Job.job_type == JobType.module_generation, Job.payload["paired_module_id"].astext == str(module.id)
    ).all()
    if any(j.status in (JobStatus.queued, JobStatus.running) for j in paired_jobs):
        raise HTTPException(
            status_code=409, detail="Bản đối chứng của mô-đun này đang được tạo. Hãy chờ xong rồi xóa."
        )
    control_ids = set(db.scalars(select(Module.id).where(Module.paired_module_id == module.id)))
    control_ids.update(db.scalars(select(Module.id).where(
        Module.generation_payload.is_(None),
        Module.id.in_([j.module_id for j in paired_jobs if j.module_id is not None]),
    )))
    check_study_references(db, {module.id, *control_ids})
    # Bulk DELETE runs now, before the RAG module goes. Deleting both through the ORM in one flush
    # raced with the paired_module_id cascade, so the control row was sometimes already gone.
    if control_ids:
        db.execute(delete(Module).where(Module.id.in_(control_ids)))
    # Job đối chứng lỗi không có module để cascade, mà giữ lại thì nó mãi trỏ tới module không còn.
    for job in paired_jobs:
        if job.module_id is None:
            db.delete(job)
    db.delete(module)
    commit_deletion(db)
    return {"status": "deleted", "deleted_module_ids": [module_id, *sorted(control_ids)]}


def _ai_original(component: ModuleComponent) -> dict:
    if "ai_original" in component.content:
        return component.content["ai_original"]
    if component.type not in (ComponentType.quiz, ComponentType.practice):
        return component.content
    return {
        "content": component.content,
        "questions": [{
            "id": str(q.id), "position": q.position, "stem": q.stem, "choices": q.choices,
            "answer_key": q.answer_key, "explanation": q.explanation,
        } for q in component.questions],
    }


class ComponentUpdate(BaseModel):
    content: dict


@router.patch("/{module_id}/components/{component_id}")
def update_component(
    module_id: uuid.UUID, component_id: uuid.UUID, payload: ComponentUpdate, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_module(db, module_id, current_user)
    component = db.get(ModuleComponent, component_id, with_for_update=True)
    if component is None or component.module_id != module_id:
        raise HTTPException(status_code=404, detail="Component not found")
    content = {k: v for k, v in payload.content.items() if k != "ai_original"}
    if component.type == ComponentType.content:
        markdown = content.get("markdown")
        if not isinstance(markdown, str):
            raise HTTPException(status_code=422, detail="Content Markdown is required")
        for key in ("source_map", "inline_citations", "claim_citations", "source_locators"):
            content.pop(key, None)
        if "source_map" in component.content:
            try:
                # The client may send metadata copied before an edit, but cannot replace the source map.
                content.update(citation_content(markdown, component.content["source_map"]))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        else:
            if MARKER.search(markdown):
                raise HTTPException(status_code=422, detail="Content without a source map must not contain citations")
            # Keep legacy display on unchanged text; changed text cannot retain copied old claims.
            unchanged = markdown == component.content.get("markdown")
            content["claim_citations"] = component.content.get("claim_citations", []) if unchanged else []
            content["source_locators"] = component.content.get("source_locators", []) if unchanged else []
    content["ai_original"] = _ai_original(component)
    component.content = content
    db.commit()
    return {"id": component.id, "content": component.content}


class QuestionUpdate(BaseModel):
    stem: str = Field(min_length=1)
    choices: list[str] = Field(min_length=2)
    answer_key: int = Field(ge=0)
    explanation: str


@router.patch("/{module_id}/questions/{question_id}")
def update_question(
    module_id: uuid.UUID, question_id: uuid.UUID, payload: QuestionUpdate, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    module = _owned_module(db, module_id, current_user)
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    component = db.get(ModuleComponent, question.component_id, with_for_update=True)
    if component.module_id != module_id or component.type not in (ComponentType.quiz, ComponentType.practice):
        raise HTTPException(status_code=404, detail="Question not found")
    _apply_question_update(module, component, question, payload)
    db.commit()
    return {"id": question.id, "stem": question.stem, "choices": question.choices,
            "answer_key": question.answer_key, "explanation": question.explanation}


def _apply_question_update(module: Module, component: ModuleComponent, question: Question,
                           payload: QuestionUpdate) -> None:
    if payload.answer_key >= len(payload.choices) or not payload.stem.strip() or any(not c.strip() for c in payload.choices):
        raise HTTPException(status_code=422, detail="Câu hỏi, phương án và đáp án không hợp lệ.")
    explanation = next((c for c in module.components if c.type == ComponentType.explanation), None)
    items = explanation.content.get("items", []) if explanation is not None else []
    # Keep the whole untouched component once; the publisher reads only the live question rows.
    component.content = {**component.content, "ai_original": _ai_original(component), "questions": [
        {**q, "passed": True, "fail_reason": None} if i == question.position else q
        for i, q in enumerate(component.content.get("questions", []))
    ]}
    question.stem = payload.stem
    question.choices = {"options": payload.choices}
    question.answer_key = str(payload.answer_key)
    question.explanation = payload.explanation
    if explanation is not None:
        explanation.content = {**explanation.content,
            "ai_original": explanation.content.get("ai_original", explanation.content), "items": [
            {**item, "explanation_text": payload.explanation}
            if item.get("component") == component.type.value and item.get("position") == question.position
            else item for item in items
        ]}


@router.post("/{module_id}/chat-edit")
@limiter.limit("20/minute", key_func=user_or_address)
def chat_edit_module(
    request: Request, module_id: uuid.UUID, payload: ChatRequest,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    module = _owned_module(db, module_id, current_user)
    if is_control(db, module.id):
        raise HTTPException(
            status_code=422,
            detail="Bản đối chứng chỉ dùng cho nghiên cứu nên không chỉnh sửa bằng AI được.",
        )
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    session = _get_or_create_session(db, module.course_id, payload.session_id, message, current_user.id, module.id)
    history_rows = db.query(ChatMessage).filter(ChatMessage.session_id == session.id).order_by(
        ChatMessage.created_at.desc(), ChatMessage.id.desc(),
    ).limit(HISTORY_TURNS).all()
    history = [{"role": r.role.value, "content": r.content} for r in reversed(history_rows)]
    scope_chunks = chunks = reference_scope_chunks(db, module)
    if chunks and not small_reference(chunks):
        chunks = retrieve(db, module.course_id, message, top_k=8, scope=ChunkScope(module.document_id))
    # The passages the content already cites stay citable: the model sees them under IDs too.
    citations = resolved_citations(db, module) or []
    cited = {c.chunk_id for c in citations} - {str(c.id) for c in chunks}
    chunks = [*chunks, *(c for c in scope_chunks if str(c.id) in cited)]
    generation = generation_payload_of(db, module.id)
    try:
        with using_model(catalog.served_or_default(generation.get("model_alias"))):
            edit = propose_module_edit(
                message, module, chunks, history, generation.get("output_language", "vi"), citations)
        applied = edit.target is not None
        if not applied:
            if edit.revised is not None or edit.question_number is not None:
                raise ValueError("Clarification must not contain an edit")
        else:
            component = next((c for c in module.components if c.type.value == edit.target), None)
            if component is None or edit.revised is None:
                raise ValueError("Missing component or revision")
            # Use the same lock and original-preserving mutations as the manual editors.
            component = db.get(ModuleComponent, component.id, with_for_update=True)
            schemas = {"content": ContentOutput, "slides": SlidesOutput, "example": ExampleOutput,
                       "quiz": QuizQuestionEdit, "practice": QuizQuestionEdit}
            revised = schemas[edit.target].model_validate(edit.revised.model_dump())
            if edit.target in ("quiz", "practice"):
                question = next((q for q in component.questions if q.position + 1 == edit.question_number), None)
                if question is None:
                    raise ValueError("Question position not found")
                _apply_question_update(module, component, question, QuestionUpdate(
                    stem=revised.stem, choices=revised.choices, answer_key=revised.correct_index,
                    explanation=revised.explanation,
                ))
            else:
                if edit.question_number is not None:
                    raise ValueError("Question number only applies to quiz/practice")
                content = revised.model_dump()
                if edit.target == "content":
                    sourced = resolve_citations(revised, chunks)
                    content = sourced.model_dump()
                    content["markdown"] = content.pop("content")
                prior = {k: v for k, v in component.content.items() if edit.target != "content" or k != "claim_citations"}
                component.content = {**prior, **content,
                                     "ai_original": component.content.get("ai_original", component.content)}
    except (ValidationError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail="AI trả lời không hợp lệ nên mô-đun chưa thay đổi. Hãy thử lại.") from exc
    db.add_all([
        ChatMessage(session_id=session.id, role=ChatRole.user, content=message, citations=[],
                    created_at=datetime.now(UTC).replace(tzinfo=None)),
        ChatMessage(session_id=session.id, role=ChatRole.assistant, content=edit.reply,
                    citations=[], applied_edit=applied, created_at=datetime.now(UTC).replace(tzinfo=None)),
    ])
    db.commit()
    return {"session_id": session.id, "reply": edit.reply, "applied": applied,
            "edit_count": module_edit_count(db, module.id)}
