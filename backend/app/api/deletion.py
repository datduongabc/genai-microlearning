from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


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
