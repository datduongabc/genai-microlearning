import json
import uuid
from datetime import UTC, datetime
from io import BytesIO
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pptx import Presentation
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.chat import ChatRequest, _get_or_create_session
from app.api.deletion import check_study_references, commit_deletion
from app.api.deps import get_current_user
from app.auth.rate_limit import limiter, user_or_address
from app.chat.service import HISTORY_TURNS, QuizQuestionEdit, module_edit_count, propose_module_edit
from app.db import get_db
from app.generation.common import OutputLanguage
from app.generation.content import ContentOutput, resolve_citations
from app.generation.example import ExampleOutput
from app.generation.inline_citations import citation_content
from app.generation.limits import reserve_generations
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
    DocumentSection,
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

# Each module costs about 19 LLM calls, so this caps how many one click can push
# into the free tier's rate limit. Mirrored by MAX_SECTIONS_PER_REQUEST in
# frontend/src/components/SectionTree.tsx.
MAX_SECTIONS_PER_REQUEST = 5


class GenerateRequest(BaseModel):
    # Far more than anyone can pick (see MAX_SECTIONS_PER_REQUEST): the cap only keeps a
    # huge list away from the queries that check it.
    section_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    # Model cho riêng lượt sinh này, ghi đè mặc định của giảng viên. Bỏ trống thì dùng
    # mặc định của họ, không có mặc định thì dùng cấu hình hệ thống.
    model_alias: str | None = None
    # Tắt vòng model tự kiểm Content và câu hỏi (thí nghiệm có/không tự kiểm cho chương đánh giá).
    self_check: bool = True
    output_language: OutputLanguage = "vi"
    custom_instructions: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""
    # False: sinh bản đối chứng không RAG (nhóm llm_no_rag) của module RAG `paired_module_id`, trên
    # đúng Learning Objective và mục của nó. Model và cờ tự kiểm bỏ trống thì theo module RAG đó.
    use_retrieval: bool = True
    paired_module_id: uuid.UUID | None = None


def _alias_da_chon(body: GenerateRequest | None, current_user: User) -> str | None:
    """Alias sẽ dùng cho lượt sinh này, hoặc None nghĩa là để cấu hình hệ thống quyết định.

    Kiểm ở đây chứ không tin client: một alias lạ lọt xuống hàng đợi thì lượt sinh chạy
    mười phút rồi mới hỏng ở lệnh gọi LLM, và lỗi chẳng nói gì về việc chọn model.
    """
    alias = body.model_alias if body is not None else None
    if alias is not None:
        return _alias_dung_duoc(alias)
    alias = current_user.default_model_alias
    if alias is None:
        # Đường mặc định: không có gì để kiểm hay thử, nên không gọi gateway lúc bấm nút.
        return None
    if not catalog.is_available(alias):
        # Mặc định đã lưu có thể trỏ tới alias gateway đã bỏ (gemini-pro đổi thành
        # gemini-flash): lùi về cấu hình hệ thống, đừng chặn mọi lượt sinh bằng 422.
        return None
    return _alias_dung_duoc(alias)


def _alias_dung_duoc(alias: str) -> str:
    """`alias` nếu gateway phục vụ nó và nó dùng được ngay lúc này, không thì 422."""
    if not catalog.is_available(alias):
        raise HTTPException(
            status_code=422,
            detail=f"Gateway không phục vụ model sinh văn bản nào tên '{alias}'.",
        )
    # Có trong danh sách chưa chắc đã dùng được: khóa của nhà cung cấp có thể trống, hết số
    # dư, hoặc hết hạn mức ngày. Thử ngay bây giờ để báo trong một giây thay vì mười phút.
    # GatewayUnavailable thì để lọt ra, app/main.py đổi nó thành 503.
    try:
        catalog.probe(alias)
    except catalog.ModelUnusable as loi:
        raise HTTPException(
            status_code=422,
            detail=f"Model '{loi.alias}' chưa dùng được: {loi.ly_do}. Hãy chọn model khác.",
        ) from loi
    return alias


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


def _generate_for_sections(
    db: Session, doc: Document, section_ids: list[uuid.UUID] | None, model_alias: str | None = None,
    self_check: bool = True,
    output_language: OutputLanguage = "vi", custom_instructions: str = "", *, current_user: User,
):
    if not section_ids:
        raise HTTPException(status_code=422, detail="Chọn ít nhất 1 mục để sinh mô-đun.")
    unique_ids = list(dict.fromkeys(section_ids))
    if len(unique_ids) > MAX_SECTIONS_PER_REQUEST:
        raise HTTPException(
            status_code=422, detail=f"Chỉ được chọn tối đa {MAX_SECTIONS_PER_REQUEST} mục mỗi lần."
        )

    found = set(
        db.scalars(
            select(DocumentSection.id).where(
                DocumentSection.document_id == doc.id, DocumentSection.id.in_(unique_ids)
            )
        )
    )
    if found != set(unique_ids):
        raise HTTPException(status_code=404, detail="Section not found")

    already_running = (
        db.query(Job.id)
        .filter(
            Job.job_type == JobType.module_generation,
            Job.section_id.in_(unique_ids),
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
        .first()
    )
    if already_running is not None:
        # All or nothing: a partial batch would leave the teacher unsure which
        # of the picked sections were actually started.
        raise HTTPException(
            status_code=409,
            detail="Có mục đang sinh mô-đun, vui lòng chờ xong rồi chọn lại.",
        )

    reserve_generations(db, current_user, len(unique_ids))
    jobs = [
        enqueue_job(
            db, job_type=JobType.module_generation, step="generate", document_id=doc.id,
            section_id=section_id, payload=generation_payload(model_alias, self_check, output_language, custom_instructions),
        )
        for section_id in unique_ids
    ]
    db.commit()
    return {"jobs": [{"section_id": j.section_id, "job_id": j.id} for j in jobs]}


def _generate_without_retrieval(db: Session, doc: Document, body: GenerateRequest, current_user: User):
    """Một job sinh bản đối chứng không RAG (nhóm llm_no_rag) của module RAG `paired_module_id`: cùng
    Learning Objective, cùng mục, và theo mặc định cùng model và cờ tự kiểm đã sinh module RAG đó,
    để khác biệt giữa hai bản chỉ còn là RAG."""
    if body.use_retrieval:
        raise HTTPException(status_code=422, detail="paired_module_id chỉ dùng khi use_retrieval=false.")
    if body.paired_module_id is None:
        raise HTTPException(
            status_code=422,
            detail="Sinh không RAG cần paired_module_id: module RAG đi cặp để lấy Learning Objective.",
        )
    if body.section_ids:
        raise HTTPException(
            status_code=422, detail="Sinh không RAG dùng đúng mục của module đi cặp, không chọn section_ids.",
        )
    paired = _owned_module(db, body.paired_module_id, current_user)
    if paired.document_id != doc.id:
        raise HTTPException(status_code=422, detail="Module đi cặp không thuộc tài liệu này.")
    try:
        learning_objective_of(paired)
    except IncompleteModuleError as loi:
        raise HTTPException(status_code=422, detail=str(loi)) from loi

    if is_control(db, paired.id):
        raise HTTPException(status_code=422, detail="Module đi cặp phải là module sinh có RAG.")
    paired_payload = generation_payload_of(db, paired.id)
    alias = body.model_alias or paired_payload.get("model_alias")
    alias = _alias_dung_duoc(alias) if alias is not None else None
    self_check = body.self_check if "self_check" in body.model_fields_set else paired_payload.get("self_check", True)

    running = (
        db.query(Job.id)
        .filter(
            Job.job_type == JobType.module_generation, Job.document_id == doc.id,
            Job.section_id.is_(None) if paired.section_id is None else Job.section_id == paired.section_id,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
        .first()
    )
    if running is not None:
        raise HTTPException(status_code=409, detail="Mục này đang có 1 lượt sinh mô-đun khác đang chạy, vui lòng chờ.")

    reserve_generations(db, current_user)
    job = enqueue_job(
        db, job_type=JobType.module_generation, step="generate", document_id=doc.id, section_id=paired.section_id,
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
    # FOR UPDATE: the "already generating" checks below read the jobs table, so
    # two requests for one document must not run them at the same moment or
    # both would pass and start the same work twice.
    doc = _owned_document(db, document_id, current_user, lock=True)
    if not doc.raw_text:
        raise HTTPException(status_code=422, detail="Document chưa được parse (raw_text rỗng)")

    if body is not None and (not body.use_retrieval or body.paired_module_id is not None):
        return _generate_without_retrieval(db, doc, body, current_user)

    alias = _alias_da_chon(body, current_user)
    self_check = body.self_check if body is not None else True

    output_language = body.output_language if body is not None else "vi"
    custom_instructions = body.custom_instructions if body is not None else ""
    section_ids = body.section_ids if body is not None else None
    has_outline = db.query(DocumentSection.id).filter(DocumentSection.document_id == doc.id).first() is not None
    if has_outline:
        return _generate_for_sections(db, doc, section_ids, alias, self_check, output_language, custom_instructions, current_user=current_user)
    if section_ids:
        raise HTTPException(status_code=422, detail="Tài liệu này không có mục lục để chọn mục.")

    # Bảng jobs giờ là nguồn sự thật DUY NHẤT cho "có đang sinh dở không" --
    # trước đây có 1 lock RIÊNG trong bộ nhớ (_generating_document_ids) cho
    # việc này; job queue tự nhiên thay thế nó, tránh 2 cơ chế song song có
    # thể lệch nhau. Chặn double-submit cùng document_id (double-click, đa
    # tab, hoặc UI mất state "đang sinh" sau khi remount page rồi bấm lại -
    # bug thật đã gặp và verify sống trong session này). Chỉ tính job sinh cả
    # tài liệu (section_id NULL): job theo mục có khóa chống trùng riêng.
    existing = (
        db.query(Job)
        .filter(
            Job.job_type == JobType.module_generation, Job.document_id == document_id,
            Job.section_id.is_(None),
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="Tài liệu này đang có 1 lượt sinh mô-đun khác đang chạy, vui lòng chờ.",
        )

    # generate_module() thật đo được mất 10+ phút (17 lệnh gọi LLM tuần tự) --
    # chạy nền qua job queue thay vì giữ request HTTP chờ suốt, xem
    # app/services/module_generation_pipeline.py + job_queue.py.
    reserve_generations(db, current_user)
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
            "section_id": m.section_id,
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


def citation_label(filename: str, locator: str) -> str:
    """A display label only: the saved locator remains exact for source resolution."""
    try:
        parsed = json.loads(locator)
    except (ValueError, TypeError):
        return filename if locator.lstrip().startswith(("{", "[")) else locator
    if not isinstance(parsed, dict):
        return filename
    spans = parsed.get("spans")
    spans = spans if isinstance(spans, list) else [parsed]
    spans = [span for span in spans if isinstance(span, dict)]
    parts = [filename]
    for key, word, offset in (
        ("page", "trang", 0),
        ("slide_index", "slide", 0),
        ("table_index", "bảng", 1),
        ("row_index", "hàng", 1),
        ("paragraph_index", "đoạn", 1),
    ):
        numbers = sorted({span[key] + offset for span in spans if isinstance(span.get(key), int)})
        ranges = []
        for number in numbers:
            if ranges and number == ranges[-1][1] + 1:
                ranges[-1][1] = number
            else:
                ranges.append([number, number])
        if ranges:
            parts.append(f"{word} " + ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in ranges))
    times = [span[key] for span in spans for key in ("start_ms", "end_ms") if isinstance(span.get(key), int)]
    if times:
        parts.append("–".join(f"{t // 60000:02d}:{t // 1000 % 60:02d}" for t in (min(times), max(times))))
    parts.append(next(
        (span[key] for span in spans for key in ("heading", "slide_title") if isinstance(span.get(key), str) and span[key]),
        None,
    ))
    return " · ".join(part for part in parts if part)


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
    CASCADE. Bảng không lưu module đã đẩy lên Moodle chưa (publish-to-moodle chỉ trả id), nên không
    có tham chiếu cục bộ nào để dọn; Book/Quiz đã tạo trên Moodle giữ nguyên, giống như xóa khóa học.
    """
    module = _owned_module(db, module_id, current_user)
    # Cùng khóa với /modules/generate: một lượt sinh bản đối chứng không được chen vào giữa lúc kiểm và lúc xóa.
    _owned_document(db, module.document_id, current_user, lock=True)

    paired_jobs = db.query(Job).filter(
        Job.job_type == JobType.module_generation, Job.payload["paired_module_id"].astext == str(module.id)
    ).all()
    if any(j.status in (JobStatus.queued, JobStatus.running) for j in paired_jobs):
        raise HTTPException(
            status_code=409, detail="Bản đối chứng của mô-đun này đang được sinh, vui lòng chờ xong rồi xóa."
        )
    control_ids = set(db.scalars(select(Module.id).where(Module.paired_module_id == module.id)))
    control_ids.update(db.scalars(select(Module.id).where(
        Module.generation_payload.is_(None),
        Module.id.in_([j.module_id for j in paired_jobs if j.module_id is not None]),
    )))
    check_study_references(db, {module.id, *control_ids})
    for control in db.query(Module).filter(Module.id.in_(control_ids)):
        db.delete(control)
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
        try:
            # The client may send metadata copied before an edit, but cannot replace the source map.
            derived = citation_content(markdown, component.content.get("source_map", {}))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for key in ("source_map", "inline_citations", "claim_citations", "source_locators"):
            content.pop(key, None)
        if "source_map" in component.content:
            content.update(derived)
        else:
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
    component.content = {**component.content, "ai_original": _ai_original(component)}
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
            detail="Mô-đun đối chứng (không RAG) chỉ dùng cho nghiên cứu, không được chỉnh sửa bằng chat.",
        )
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    session = _get_or_create_session(db, module.course_id, payload.session_id, message, module.id)
    history_rows = db.query(ChatMessage).filter(ChatMessage.session_id == session.id).order_by(
        ChatMessage.created_at.desc(), ChatMessage.id.desc(),
    ).limit(HISTORY_TURNS).all()
    history = [{"role": r.role.value, "content": r.content} for r in reversed(history_rows)]
    section = db.get(DocumentSection, module.section_id) if module.section_id else None
    scope_chunks = chunks = reference_scope_chunks(db, module, section)
    if chunks and not small_reference(chunks):
        chunks = retrieve(db, module.course_id, message, top_k=8, scope=ChunkScope(
            module.document_id, chunks[0].chunk_index, chunks[-1].chunk_index))
    # The passages the content already cites stay citable: the model sees them under IDs too.
    citations = resolved_citations(db, module) or []
    cited = {c.chunk_id for c in citations} - {str(c.id) for c in chunks}
    chunks = [*chunks, *(c for c in scope_chunks if str(c.id) in cited)]
    generation = generation_payload_of(db, module.id)
    try:
        with using_model(generation.get("model_alias")):
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
        raise HTTPException(status_code=422, detail="Phản hồi AI không hợp lệ; chưa thay đổi mô-đun.") from exc
    db.add_all([
        ChatMessage(session_id=session.id, role=ChatRole.user, content=message, citations=[],
                    created_at=datetime.now(UTC).replace(tzinfo=None)),
        ChatMessage(session_id=session.id, role=ChatRole.assistant, content=edit.reply,
                    citations=[], applied_edit=applied, created_at=datetime.now(UTC).replace(tzinfo=None)),
    ])
    db.commit()
    return {"session_id": session.id, "reply": edit.reply, "applied": applied,
            "edit_count": module_edit_count(db, module.id)}
