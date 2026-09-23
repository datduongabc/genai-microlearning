import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_course_owner
from app.auth.rate_limit import limiter, user_or_address
from app.config import settings
from app.db import get_db
from app.ingestion.document_readers import SUPPORTED_EXTENSIONS
from app.models import (
    Course,
    Document,
    DocumentKind,
    DocumentSection,
    DocumentStatus,
    Job,
    JobStatus,
    JobType,
    Module,
    User,
)
from app.services.job_queue import enqueue_job

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(get_current_user)])

# 50MB đủ cho slide/PDF bài giảng thật (tài liệu lớn nhất đã test trong
# session này < 5MB), nhưng video bài giảng gốc (trước khi transcode xuống
# chỉ còn audio track) có thể lớn hơn nhiều -- nới lên 500MB riêng cho
# audio/video, giữ nguyên 50MB cho các định dạng còn lại. Chặn ở đây trước
# khi đọc hết vào bộ nhớ -- trước đây file.file.read() không giới hạn.
MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_SIZE_BYTES_AUDIO_VIDEO = 500 * 1024 * 1024
_READ_CHUNK_SIZE = 1024 * 1024
# A document in one of these states still has its pipeline running or queued.
_PIPELINE_ACTIVE_STATUSES = (DocumentStatus.uploaded, DocumentStatus.processing)
_AUDIO_VIDEO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac",
    ".mp4", ".mkv", ".webm", ".mov", ".avi",
}


@router.post("")
@limiter.limit("20/minute", key_func=user_or_address)
def upload_document(
    request: Request, course_id: uuid.UUID, file: UploadFile,
    kind: DocumentKind = DocumentKind.lecture, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # FOR UPDATE serialises concurrent uploads to the same course: the second
    # request waits, then sees the first document and gets the 409 below
    # instead of both passing the check.
    require_course_owner(db.get(Course, course_id, with_for_update=True), current_user)

    still_processing = (
        db.query(Document.id)
        .filter(Document.course_id == course_id, Document.status.in_(_PIPELINE_ACTIVE_STATUSES))
        .first()
    )
    if still_processing is not None:
        raise HTTPException(
            status_code=409,
            detail="Môn học này đang có tài liệu khác được xử lý, vui lòng chờ xong rồi tải tiếp.",
        )

    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"Định dạng không hỗ trợ: {suffix}")

    size_limit = (
        MAX_UPLOAD_SIZE_BYTES_AUDIO_VIDEO if suffix in _AUDIO_VIDEO_EXTENSIONS else MAX_UPLOAD_SIZE_BYTES
    )
    # Đọc theo từng khối và chặn sớm nếu vượt giới hạn, thay vì file.file.read()
    # không giới hạn (đọc hết file vào bộ nhớ trước khi kiểm tra bất cứ gì).
    content = bytearray()
    while file_chunk := file.file.read(_READ_CHUNK_SIZE):
        content.extend(file_chunk)
        if len(content) > size_limit:
            raise HTTPException(
                status_code=413,
                detail=f"File vượt quá giới hạn {size_limit // (1024 * 1024)}MB",
            )

    doc = Document(course_id=course_id, filename=file.filename, status=DocumentStatus.uploaded, kind=kind)
    db.add(doc)
    db.flush()

    # Lưu bền vững (không phải NamedTemporaryFile bị xóa cuối request) -- job
    # extract chạy nền, có thể sau khi request upload này đã trả response từ
    # lâu, nên file phải còn tồn tại lúc đó. Nằm trong bind mount ./backend:/app
    # nên sống sót qua restart container (không phải named volume ephemeral).
    upload_dir = Path(settings.upload_storage_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    storage_path = str(upload_dir / f"{doc.id}{suffix}")
    with open(storage_path, "wb") as f:
        f.write(content)

    job = enqueue_job(
        db, job_type=JobType.document_pipeline, step="extract",
        document_id=doc.id, payload={"storage_path": storage_path},
    )
    db.commit()

    return {"id": doc.id, "status": doc.status, "job_id": job.id}


@router.get("/jobs/{job_id}")
def get_document_job_status(
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
        "document_status": doc.status,
    }


@router.get("")
def list_documents(
    course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    require_course_owner(db.get(Course, course_id), current_user)

    docs = (
        db.query(Document)
        # trước đây len(d.modules) bên dưới lazy-load 1 truy vấn/document
        # (N+1) -- eager-load 1 lần cho toàn bộ danh sách.
        .options(selectinload(Document.modules))
        .filter(Document.course_id == course_id)
        .order_by(Document.created_at.desc())
        .all()
    )
    # The jobs table is the source of truth for "a module is being generated"
    # (the generate endpoint's 409 guard reads it too), so the UI can show it
    # after a refresh or from another tab.
    generating_ids = set(
        db.scalars(
            select(Job.document_id).where(
                Job.job_type == JobType.module_generation,
                Job.status.in_((JobStatus.queued, JobStatus.running)),
                Job.document_id.in_([d.id for d in docs]),
            )
        )
    )
    section_counts = dict(
        db.query(DocumentSection.document_id, func.count(DocumentSection.id))
        .filter(DocumentSection.document_id.in_([d.id for d in docs]))
        .group_by(DocumentSection.document_id)
        .all()
    )
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "status": d.status,
            "created_at": d.created_at,
            "module_count": len(d.modules),
            "generating": d.id in generating_ids,
            "kind": d.kind,
            "section_count": section_counts.get(d.id, 0),
        }
        for d in docs
    ]


@router.get("/{document_id}/sections")
def list_sections(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    doc = db.get(Document, document_id)
    if doc is None or doc.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    sections = (
        db.query(DocumentSection)
        .filter(DocumentSection.document_id == document_id)
        .order_by(DocumentSection.position)
        .all()
    )
    module_counts = dict(
        db.query(Module.section_id, func.count(Module.id))
        .filter(Module.document_id == document_id, Module.section_id.isnot(None))
        .group_by(Module.section_id)
        .all()
    )
    # Same source of truth as the document list: the jobs table.
    generating_ids = set(
        db.scalars(
            select(Job.section_id).where(
                Job.job_type == JobType.module_generation,
                Job.status.in_((JobStatus.queued, JobStatus.running)),
                Job.document_id == document_id,
                Job.section_id.isnot(None),
            )
        )
    )
    return [
        {
            "id": s.id,
            "parent_id": s.parent_id,
            "title": s.title,
            "level": s.level,
            "page_start": s.page_start,
            "page_end": s.page_end,
            "module_count": module_counts.get(s.id, 0),
            "generating": s.id in generating_ids,
        }
        for s in sections
    ]


@router.delete("/{document_id}")
def delete_document(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Cascades to chunks/modules/quizzes/evaluations via ON DELETE CASCADE
    (migration 3d8f2a1c6e9b). Also removes the uploaded file from disk if
    the pipeline never got far enough to clean it up itself (document_pipeline
    only deletes it after a successful embed step)."""
    doc = db.get(Document, document_id)
    if doc is None or doc.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    upload_dir = Path(settings.upload_storage_dir)
    for leftover in upload_dir.glob(f"{document_id}.*"):
        leftover.unlink(missing_ok=True)

    db.delete(doc)
    db.commit()
    return {"status": "deleted"}


@router.get("/{document_id}/status")
def get_document_status(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    doc = db.get(Document, document_id)
    if doc is None or doc.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"id": doc.id, "status": doc.status}
