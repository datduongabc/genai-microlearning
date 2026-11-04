import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deletion import check_study_references, commit_deletion
from app.api.deps import get_current_user, require_course_owner
from app.auth.rate_limit import limiter, user_or_address
from app.config import settings
from app.db import get_db
from app.ingestion.document_readers import (
    LEGACY_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    InputLimitError,
    preflight_limits,
)
from app.jobs.failures import open_failures
from app.jobs.queue import enqueue_job
from app.models import (
    Course,
    Document,
    DocumentStatus,
    Job,
    JobStatus,
    JobType,
    Module,
    User,
)

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(get_current_user)])

# Giới hạn đã chốt: tài liệu 25 MB, audio 200 MB, video 1 GB.
MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_SIZE_BYTES_AUDIO = 200 * 1024 * 1024
MAX_UPLOAD_SIZE_BYTES_VIDEO = 1024 * 1024 * 1024
_READ_CHUNK_SIZE = 1024 * 1024
# A document in one of these states still has its pipeline running or queued.
_PIPELINE_ACTIVE_STATUSES = (DocumentStatus.uploaded, DocumentStatus.processing)
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi"}


@router.post("")
@limiter.limit("20/minute", key_func=user_or_address)
def upload_document(
    request: Request, course_id: uuid.UUID, file: UploadFile,
    db: Session = Depends(get_db),
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
    if suffix in LEGACY_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Định dạng {suffix} đã cũ và không được hỗ trợ. "
                   f"Hãy mở tệp rồi lưu lại thành {LEGACY_EXTENSIONS[suffix]} (Save As) trước khi tải lên.",
        )
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Định dạng {suffix or '(không có đuôi)'} không được hỗ trợ. "
                   f"Các định dạng được nhận: {', '.join(sorted(SUPPORTED_EXTENSIONS))}.",
        )

    if suffix in _AUDIO_EXTENSIONS:
        size_limit, size_error = MAX_UPLOAD_SIZE_BYTES_AUDIO, "Tệp âm thanh tối đa 200 MB"
    elif suffix in _VIDEO_EXTENSIONS:
        size_limit, size_error = MAX_UPLOAD_SIZE_BYTES_VIDEO, "Tệp video tối đa 1 GB"
    else:
        size_limit, size_error = MAX_UPLOAD_SIZE_BYTES, "Tài liệu tối đa 25 MB"
    # Ghi thẳng từng khối xuống đĩa và chặn sớm nếu vượt giới hạn: video tới 1 GB thì
    # không được giữ cả tệp trong bộ nhớ.
    upload_dir = Path(settings.upload_storage_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    pending_path = upload_dir / f"pending-{uuid.uuid4()}{suffix}"
    try:
        size = 0
        with open(pending_path, "wb") as f:
            while file_chunk := file.file.read(_READ_CHUNK_SIZE):
                size += len(file_chunk)
                if size > size_limit:
                    raise HTTPException(status_code=413, detail=size_error)
                f.write(file_chunk)
        # Đếm trang/đo thời lượng trước khi tạo job, để job nền không phải tải hay
        # phiên âm một tệp chắc chắn bị từ chối.
        try:
            preflight_limits(str(pending_path))
        except InputLimitError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc

        doc = Document(course_id=course_id, filename=file.filename, status=DocumentStatus.uploaded)
        db.add(doc)
        db.flush()

        # Lưu bền vững (không phải NamedTemporaryFile bị xóa cuối request) -- job
        # extract chạy nền, có thể sau khi request upload này đã trả response từ
        # lâu, nên file phải còn tồn tại lúc đó. Nằm trong bind mount ./backend:/app
        # nên sống sót qua restart container (không phải named volume ephemeral).
        storage_path = str(upload_dir / f"{doc.id}{suffix}")
        os.replace(pending_path, storage_path)
    except BaseException:
        pending_path.unlink(missing_ok=True)
        raise

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
    # Failed work stays on its document after a reload: a failed generation creates no module,
    # so without this its error would only ever be a banner that the next refresh loses.
    failures = open_failures(db, [d.id for d in docs])
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "status": d.status,
            "created_at": d.created_at,
            "module_count": len(d.modules),
            "generating": d.id in generating_ids,
            "failures": failures.get(d.id, []),
            "extraction_warnings": d.extraction_warnings,
        }
        for d in docs
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

    check_study_references(db, db.scalars(select(Module.id).where(Module.document_id == document_id)))
    db.delete(doc)
    commit_deletion(db)

    upload_dir = Path(settings.upload_storage_dir)
    for leftover in upload_dir.glob(f"{document_id}.*"):
        leftover.unlink(missing_ok=True)

    return {"status": "deleted"}


@router.get("/{document_id}/status")
def get_document_status(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    doc = db.get(Document, document_id)
    if doc is None or doc.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"id": doc.id, "status": doc.status}
