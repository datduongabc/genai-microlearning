"""A study's reference files: the admin uploads them and picks one per packet; a rater downloads
the one of the packet they are rating.

The upload goes the way a course document does (app/api/documents.py): request, send the file
(straight to S3 by a presigned POST, or to PUT .../content without S3), complete. A download is a
short-lived S3 link, or without S3 the API sends the file itself.
"""
import uuid
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.api.documents import _checked_suffix, _size_limit
from app.db import get_db
from app.models import Study, StudyFile, StudyItem, User
from app.storage import LocalUploadStore, S3UploadStore, staging_dir, study_file_key, study_file_store

router = APIRouter(prefix="/studies", tags=["studies"])
Filename = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class FileRequest(BaseModel):
    filename: Filename
    size: int = Field(gt=0)


class Reference(BaseModel):
    file_id: uuid.UUID | None


def _file_out(file: StudyFile) -> dict:
    return {"id": file.id, "filename": file.filename, "size": file.size, "created_at": file.created_at}


def _study(db: Session, study_id: uuid.UUID) -> Study:
    study = db.get(Study, study_id)
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    return study


def _file(db: Session, study_id: uuid.UUID, file_id: uuid.UUID, *, status: str | None = "ready") -> StudyFile:
    file = db.get(StudyFile, file_id)
    if file is None or file.study_id != study_id or (status is not None and file.status != status):
        raise HTTPException(status_code=404, detail="File not found")
    return file


def _download(key: str, filename: str, api_path: str) -> dict:
    """Where the browser gets the file: an S3 link it follows itself, or, without S3, an API path
    it fetches with its token."""
    store = study_file_store()
    if isinstance(store, S3UploadStore):
        return {"url": store.presigned_get(key, filename), "via_api": False, "filename": filename}
    return {"url": api_path, "via_api": True, "filename": filename}


def _send(key: str, filename: str) -> FileResponse:
    store = study_file_store()
    if not isinstance(store, LocalUploadStore):
        raise HTTPException(status_code=404, detail="Not found")
    path = store.path(key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=filename)


@router.get("/{study_id}/files", dependencies=[Depends(require_admin)])
def list_files(study_id: uuid.UUID, db: Session = Depends(get_db)):
    _study(db, study_id)
    files = db.scalars(select(StudyFile).where(StudyFile.study_id == study_id, StudyFile.status == "ready")
                       .order_by(StudyFile.created_at, StudyFile.id))
    return [_file_out(file) for file in files]


@router.post("/{study_id}/files", status_code=201, dependencies=[Depends(require_admin)])
def request_file(study_id: uuid.UUID, body: FileRequest, db: Session = Depends(get_db)):
    """Step 1 of 3: the same formats and limits as a course document, checked before any transfer."""
    _study(db, study_id)
    suffix = _checked_suffix(body.filename)
    size_limit, size_error = _size_limit(suffix)
    if body.size > size_limit:
        raise HTTPException(status_code=413, detail=size_error)
    file = StudyFile(study_id=study_id, filename=body.filename, size=body.size)
    db.add(file)
    db.commit()
    store = study_file_store()
    if isinstance(store, S3UploadStore):
        target = {"method": "POST", **store.presigned_post(study_file_key(file.id, file.filename), body.size)}
    else:
        target = {"method": "PUT", "url": f"/studies/{study_id}/files/{file.id}/content", "fields": {}}
    return {"file_id": file.id, "upload": target}


@router.put("/{study_id}/files/{file_id}/content")
async def put_file_content(study_id: uuid.UUID, file_id: uuid.UUID, request: Request,
                           db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Step 2 without S3: the file as the raw body, written block by block and stopped at the limit."""
    if not isinstance(study_file_store(), LocalUploadStore):
        raise HTTPException(status_code=404, detail="Not found")

    def awaiting() -> str:
        filename = _file(db, study_id, file_id, status="awaiting_upload").filename
        db.commit()
        return filename

    filename = await run_in_threadpool(awaiting)
    suffix = Path(filename).suffix.lower()
    size_limit, size_error = _size_limit(suffix)
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
        await run_in_threadpool(study_file_store().save, pending_path, study_file_key(file_id, filename))
    finally:
        pending_path.unlink(missing_ok=True)
    return {"id": file_id, "size": size}


@router.post("/{study_id}/files/{file_id}/complete", dependencies=[Depends(require_admin)])
def complete_file(study_id: uuid.UUID, file_id: uuid.UUID, db: Session = Depends(get_db)):
    """Step 3 of 3: the file is in the store; record its real size and offer it."""
    file = _file(db, study_id, file_id, status="awaiting_upload")
    store, key = study_file_store(), study_file_key(file.id, file.filename)
    size = store.size(key)
    if size is None:
        raise HTTPException(status_code=409, detail="Chưa nhận được tệp, hãy tải lên trước.")
    size_limit, size_error = _size_limit(Path(file.filename).suffix.lower())
    if size > size_limit:
        db.delete(file)
        db.commit()
        store.delete(key)
        raise HTTPException(status_code=413, detail=size_error)
    file.size, file.status = size, "ready"
    db.commit()
    return _file_out(file)


@router.delete("/{study_id}/files/{file_id}", dependencies=[Depends(require_admin)])
def delete_file(study_id: uuid.UUID, file_id: uuid.UUID, db: Session = Depends(get_db)):
    """The packets that used it lose their reference (ON DELETE SET NULL)."""
    file = _file(db, study_id, file_id, status=None)
    key = study_file_key(file.id, file.filename)
    db.delete(file)
    db.commit()
    study_file_store().delete(key)
    return {"status": "deleted"}


@router.get("/{study_id}/files/{file_id}/download", dependencies=[Depends(require_admin)])
def admin_download(study_id: uuid.UUID, file_id: uuid.UUID, db: Session = Depends(get_db)):
    file = _file(db, study_id, file_id)
    return _download(study_file_key(file.id, file.filename), file.filename,
                     f"/studies/{study_id}/files/{file_id}/content")


@router.get("/{study_id}/files/{file_id}/content", dependencies=[Depends(require_admin)])
def admin_content(study_id: uuid.UUID, file_id: uuid.UUID, db: Session = Depends(get_db)):
    file = _file(db, study_id, file_id)
    return _send(study_file_key(file.id, file.filename), file.filename)


@router.put("/{study_id}/items/{item_id}/reference", dependencies=[Depends(require_admin)])
def set_reference(study_id: uuid.UUID, item_id: uuid.UUID, body: Reference, db: Session = Depends(get_db)):
    """Sets the packet's file, and its pair partner's: a RAG and a no-RAG packet judged against
    different material would no longer be a matched pair, and a file only one of them had would
    tell the rater which is which."""
    item = db.get(StudyItem, item_id)
    if item is None or item.study_id != study_id:
        raise HTTPException(status_code=404, detail="Study item not found")
    if body.file_id is not None:
        _file(db, study_id, body.file_id)
    members = StudyItem.id == item.id if item.pair_id is None else StudyItem.pair_id == item.pair_id
    db.execute(update(StudyItem).where(StudyItem.study_id == study_id, members)
               .values(reference_file_id=body.file_id))
    db.commit()
    return [{"id": row.id, "reference_file_id": row.reference_file_id} for row in db.scalars(
        select(StudyItem).where(StudyItem.study_id == study_id).order_by(StudyItem.anon_code))]


def _rater_file(db: Session, study_id: uuid.UUID, item_id: uuid.UUID) -> dict:
    """Through the blind views, so only an invited rater finds it, as for the packet itself."""
    row = db.execute(text("""
        SELECT f.id, f.filename FROM blind_study_items i JOIN blind_study_files f ON f.id = i.reference_file_id
        WHERE i.study_id = :sid AND i.id = :iid
    """), {"sid": study_id, "iid": item_id}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Reference file not found")
    return dict(row)


@router.get("/mine/{study_id}/items/{item_id}/reference")
def rater_download(study_id: uuid.UUID, item_id: uuid.UUID, db: Session = Depends(get_db),
                   _: User = Depends(get_current_user)):
    file = _rater_file(db, study_id, item_id)
    return _download(study_file_key(file["id"], file["filename"]), file["filename"],
                     f"/studies/mine/{study_id}/items/{item_id}/reference/content")


@router.get("/mine/{study_id}/items/{item_id}/reference/content")
def rater_content(study_id: uuid.UUID, item_id: uuid.UUID, db: Session = Depends(get_db),
                  _: User = Depends(get_current_user)):
    file = _rater_file(db, study_id, item_id)
    return _send(study_file_key(file["id"], file["filename"]), file["filename"])
