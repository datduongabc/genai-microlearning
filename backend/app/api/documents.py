import uuid
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deletion import check_study_references, commit_deletion, refuse_while_working
from app.api.deps import get_current_user, require_author, require_course_owner
from app.api.media_common import document_passages
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.ingestion.document_readers import (
    LEGACY_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
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
from app.storage import (
    LocalUploadStore,
    S3UploadStore,
    delete_media_files,
    media_keys_for_document,
    staging_dir,
    upload_key,
    upload_store,
)

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(get_current_user)])

# Giới hạn đã chốt: tài liệu 25 MB, audio 200 MB, video 1 GB.
MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_SIZE_BYTES_AUDIO = 200 * 1024 * 1024
MAX_UPLOAD_SIZE_BYTES_VIDEO = 1024 * 1024 * 1024
# A document in one of these states still has its pipeline running or queued.
_PIPELINE_ACTIVE_STATUSES = (DocumentStatus.uploaded, DocumentStatus.processing)
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi"}


def _size_limit(suffix: str) -> tuple[int, str]:
    if suffix in _AUDIO_EXTENSIONS:
        return MAX_UPLOAD_SIZE_BYTES_AUDIO, "Tệp âm thanh tối đa 200 MB"
    if suffix in _VIDEO_EXTENSIONS:
        return MAX_UPLOAD_SIZE_BYTES_VIDEO, "Tệp video tối đa 1 GB"
    return MAX_UPLOAD_SIZE_BYTES, "Tài liệu tối đa 25 MB"


def _checked_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
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
    return suffix


def _refuse_while_processing(db: Session, course_id: uuid.UUID, *, other_than: uuid.UUID | None = None) -> None:
    query = db.query(Document.id).filter(
        Document.course_id == course_id, Document.status.in_(_PIPELINE_ACTIVE_STATUSES),
    )
    if other_than is not None:
        query = query.filter(Document.id != other_than)
    if query.first() is not None:
        raise HTTPException(
            status_code=409,
            detail="Môn học này đang có tài liệu khác được xử lý, vui lòng chờ xong rồi tải tiếp.",
        )


def _owned_document(db: Session, document_id: uuid.UUID, user: User, *, lock_course: bool = False) -> Document:
    doc = db.get(Document, document_id)
    if doc is None or doc.course.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    if lock_course:
        # An explicit SELECT: the course is already loaded, and a get() could answer from the session.
        db.execute(select(Course.id).where(Course.id == doc.course_id).with_for_update())
    return doc


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)


@router.post("/uploads")
@limiter.limit("20/minute", key_func=user_or_address)
def request_upload(
    request: Request, course_id: uuid.UUID, body: UploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Step 1 of 3: create the document and say where its file goes.

    The checks that need no file run here, so a wrong format or a size over the limit costs no
    transfer. The page count and the duration need the file: the worker checks them before
    reading (app/ingestion/document_pipeline.py)."""
    require_course_owner(db.get(Course, course_id), current_user)
    _refuse_while_processing(db, course_id)
    suffix = _checked_suffix(body.filename)
    size_limit, size_error = _size_limit(suffix)
    if body.size > size_limit:
        raise HTTPException(status_code=413, detail=size_error)

    doc = Document(course_id=course_id, filename=body.filename, status=DocumentStatus.awaiting_upload)
    db.add(doc)
    db.commit()

    store = upload_store()
    if isinstance(store, S3UploadStore):
        target = {"method": "POST", **store.presigned_post(upload_key(doc.id, body.filename), body.size)}
    else:
        target = {"method": "PUT", "url": f"/documents/{doc.id}/content", "fields": {}}
    return {"document_id": doc.id, "upload": target}


@router.put("/{document_id}/content")
async def put_document_content(
    document_id: uuid.UUID, request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Step 2 without S3: the file itself, as the raw request body.

    Where S3 is the store the browser posts to S3 instead, so this answers 404 there. No
    transaction is open while the body streams in, so a slow upload holds no database
    connection."""
    if not isinstance(upload_store(), LocalUploadStore):
        raise HTTPException(status_code=404, detail="Not found")

    def owned_awaiting() -> str:
        doc = _owned_document(db, document_id, current_user)
        if doc.status != DocumentStatus.awaiting_upload:
            raise HTTPException(status_code=409, detail="Tài liệu này đã được tải lên.")
        filename = doc.filename
        db.commit()
        return filename

    filename = await run_in_threadpool(owned_awaiting)
    suffix = Path(filename).suffix.lower()
    size_limit, size_error = _size_limit(suffix)
    # Written to disk block by block and stopped as soon as it passes the limit: a 1 GB video
    # is never held in memory.
    pending_path = staging_dir() / f"pending-{uuid.uuid4()}{suffix}"
    try:
        size = 0
        async with await anyio.open_file(pending_path, "wb") as f:
            async for block in request.stream():
                size += len(block)
                if size > size_limit:
                    raise HTTPException(status_code=413, detail=size_error)
                await f.write(block)
        if size == 0:
            raise HTTPException(status_code=422, detail="Tệp rỗng.")
        await run_in_threadpool(upload_store().save, pending_path, upload_key(document_id, filename))
    finally:
        pending_path.unlink(missing_ok=True)
    return {"id": document_id, "size": size}


@router.post("/{document_id}/complete")
def complete_upload(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Step 3 of 3: the file is in the store; check its size and queue the pipeline.

    The course row is locked only here, for one query and an insert, so that two uploads that
    finish together cannot both pass the one-document-at-a-time check."""
    doc = _owned_document(db, document_id, current_user, lock_course=True)
    if doc.status != DocumentStatus.awaiting_upload:
        raise HTTPException(status_code=409, detail="Tài liệu này đã được tải lên.")
    store, storage_key = upload_store(), upload_key(doc.id, doc.filename)

    size = store.size(storage_key)
    if size is None:
        # The client may call too early or retry after a failed upload: keep the document.
        raise HTTPException(status_code=409, detail="Chưa nhận được tệp, hãy tải lên trước.")
    size_limit, size_error = _size_limit(Path(doc.filename).suffix.lower())
    try:
        if size > size_limit:
            # S3 already refuses a larger POST; this is the last guard, e.g. for a stale policy.
            raise HTTPException(status_code=413, detail=size_error)
        _refuse_while_processing(db, doc.course_id, other_than=doc.id)
    except HTTPException:
        db.delete(doc)
        db.commit()
        store.delete(storage_key)
        raise

    doc.status = DocumentStatus.uploaded
    job = enqueue_job(
        db, job_type=JobType.document_pipeline, step="extract",
        document_id=doc.id, payload={"storage_key": storage_key},
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
        # A document whose file never arrived is not one yet; the hourly clean-up removes it.
        .filter(Document.course_id == course_id, Document.status != DocumentStatus.awaiting_upload)
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
    refuse_while_working(db, [document_id])
    storage_key = upload_key(document_id, doc.filename)
    media_keys = media_keys_for_document(db, document_id)
    db.delete(doc)
    commit_deletion(db)

    # Committed first: a file without its row is harmless, a row whose file is gone is not.
    upload_store().delete(storage_key)
    delete_media_files(media_keys)

    return {"status": "deleted"}


@router.get("/{document_id}/chunks")
def list_document_chunks(
    document_id: uuid.UUID,
    q: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The document's passages, for a lecturer citing one in a flashcard or a video scene they wrote."""
    require_author(current_user)
    return document_passages(db, _owned_document(db, document_id, current_user), q, offset, limit)


@router.get("/{document_id}/status")
def get_document_status(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    doc = db.get(Document, document_id)
    if doc is None or doc.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"id": doc.id, "status": doc.status}
