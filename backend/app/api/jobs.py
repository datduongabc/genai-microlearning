from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models import Course, Document, Job, JobStatus, JobType, User

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(get_current_user)])


@router.get("/failed")
def list_failed_jobs(
    job_type: JobType | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Backs the monitoring dashboard -- surfaces real pipeline failures
    (document extraction/chunking/embedding, module generation) instead of
    relying on unit test coverage to catch issues that only show up with
    real documents. Scoped to the caller's own courses here, and again by RLS
    (via get_db()): each layer has to hold on its own.
    """
    query = (
        db.query(Job, Document.filename, Course.name)
        .join(Document, Job.document_id == Document.id)
        .join(Course, Document.course_id == Course.id)
        .filter(Job.status == JobStatus.failed, Course.owner_id == current_user.id)
    )
    if job_type is not None:
        query = query.filter(Job.job_type == job_type)
    rows = query.order_by(Job.created_at.desc()).limit(200).all()

    return [
        {
            "id": job.id,
            "job_type": job.job_type,
            "step": job.step,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "last_error": job.last_error,
            "created_at": job.created_at,
            "document_filename": filename,
            "course_name": course_name,
            "module_id": job.module_id,
        }
        for job, filename, course_name in rows
    ]
