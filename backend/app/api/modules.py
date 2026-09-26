import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.deps import get_current_user
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.generation.module_generation_pipeline import (
    IncompleteModuleError,
    generation_payload_of,
    learning_objective_of,
)
from app.jobs.queue import enqueue_job
from app.llm import catalog
from app.models import (
    ComponentType,
    Document,
    DocumentSection,
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
    User,
)

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


def _payload_model(alias: str | None, self_check: bool = True) -> dict:
    """Không ghi alias khi chưa chọn, thay vì ghi sẵn giá trị từ cấu hình: ghi sẵn thì
    đổi cấu hình hệ thống sẽ không tác dụng với các job đã nằm trong hàng đợi. `self_check`
    luôn được ghi, để biết chắc mỗi module của thí nghiệm thuộc nhánh nào."""
    payload: dict = {"self_check": self_check}
    if alias:
        payload["model_alias"] = alias
    return payload


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

    jobs = [
        enqueue_job(
            db, job_type=JobType.module_generation, step="generate", document_id=doc.id,
            section_id=section_id, payload=_payload_model(model_alias, self_check),
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

    paired_payload = generation_payload_of(db, paired.id)
    if paired_payload.get("use_retrieval", True) is False:
        raise HTTPException(status_code=422, detail="Module đi cặp phải là module sinh có RAG.")
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

    job = enqueue_job(
        db, job_type=JobType.module_generation, step="generate", document_id=doc.id, section_id=paired.section_id,
        payload={**_payload_model(alias, self_check), "use_retrieval": False, "paired_module_id": str(paired.id)},
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

    section_ids = body.section_ids if body is not None else None
    has_outline = db.query(DocumentSection.id).filter(DocumentSection.document_id == doc.id).first() is not None
    if has_outline:
        return _generate_for_sections(db, doc, section_ids, alias, self_check)
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
    job = enqueue_job(
        db, job_type=JobType.module_generation, step="generate", document_id=document_id,
        payload=_payload_model(alias, self_check),
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
    return {
        "id": module.id,
        "title": module.title,
        "learning_objective": module.learning_objective,
        "bloom_level": module.bloom_level,
        "sub_objectives": module.sub_objectives,
        "course_id": module.course_id,
        "moodle_course_id": module.course.moodle_course_id,
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


class ComponentUpdate(BaseModel):
    content: dict


@router.patch("/{module_id}/components/{component_id}")
def update_component(
    module_id: uuid.UUID, component_id: uuid.UUID, payload: ComponentUpdate, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_module(db, module_id, current_user)
    component = db.get(ModuleComponent, component_id)
    if component is None or component.module_id != module_id:
        raise HTTPException(status_code=404, detail="Component not found")
    component.content = payload.content
    db.commit()
    return {"id": component.id, "content": component.content}
