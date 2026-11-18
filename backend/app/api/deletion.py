from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Job, JobStatus


def check_study_references(db: Session, module_ids) -> None:
    if db.scalar(text("SELECT modules_referenced_by_study(CAST(:ids AS uuid[]))"),
                 {"ids": list(module_ids)}):
        raise HTTPException(status_code=409, detail="Module is referenced by a study")


def commit_deletion(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if getattr(error.orig, "sqlstate", None) == "23503":
            raise HTTPException(status_code=409, detail="Deletion blocked by retained references") from error
        raise


def refuse_while_working(db: Session, document_ids) -> None:
    """409 while a job of these documents is running: it would keep calling paid models after its
    rows are gone, then drop the result, and a render would leave its file behind. A queued job is
    deleted with them before any worker takes it, so a file uploaded by mistake can go at once."""
    busy = db.scalar(select(Job.id).where(Job.document_id.in_(document_ids), Job.status == JobStatus.running).limit(1))
    if busy is not None:
        raise HTTPException(status_code=409, detail="Tài liệu đang được xử lý hoặc đang sinh nội dung, vui lòng chờ xong rồi xóa.")
