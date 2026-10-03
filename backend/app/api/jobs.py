import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.jobs.queue import retry_failed_job
from app.models import Document, Job, JobStatus, User

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(get_current_user)])


@router.post("/{job_id}/retry")
def retry_job(job_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The lecturer's "Thử lại" on their own failed document or failed module generation: the
    same job runs again from where it stopped, with nothing re-uploaded and no extra module.
    What is not theirs answers 404 like what does not exist, by the ownership check here and by
    RLS (via get_db()): each layer has to hold on its own."""
    job = db.get(Job, job_id)
    doc = db.get(Document, job.document_id) if job is not None and job.document_id is not None else None
    if doc is None or doc.course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    if not retry_failed_job(db, job_id):
        raise HTTPException(status_code=409, detail="Công việc này không còn ở trạng thái lỗi để thử lại.")
    db.commit()
    return {"id": job_id, "status": JobStatus.queued}
