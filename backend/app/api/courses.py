import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deletion import check_study_references, commit_deletion
from app.api.deps import get_current_user
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.models import Course, Document, Module, User
from app.moodle import client as moodle_client
from app.moodle.accounts import require_course_author, teacher_courses
from app.moodle.errors import MOODLE_FAILURES, moodle_failure
from app.storage import upload_key, upload_store

router = APIRouter(prefix="/courses", tags=["courses"], dependencies=[Depends(get_current_user)])


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Tên khóa học không được để trống")
        return v


class LinkMoodleCourse(BaseModel):
    # The range of a Moodle course id (a 32-bit integer, starting at 1).
    moodle_course_id: int = Field(ge=1, le=2_147_483_647)


def _get_owned_course(course_id: uuid.UUID, db: Session, current_user: User) -> Course:
    course = db.get(Course, course_id)
    if course is None or course.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


@router.post("")
@limiter.limit("30/minute", key_func=user_or_address)
def create_course(
    request: Request, payload: CourseCreate, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    course = Course(name=payload.name, owner_id=current_user.id)
    db.add(course)
    db.commit()
    db.refresh(course)
    return {"id": course.id, "name": course.name, "created_at": course.created_at, "moodle_course_id": None}


@router.get("")
def list_courses(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    courses = (
        db.query(Course)
        .filter(Course.owner_id == current_user.id)
        .order_by(Course.created_at.desc())
        .all()
    )
    return [
        {"id": c.id, "name": c.name, "created_at": c.created_at, "moodle_course_id": c.moodle_course_id}
        for c in courses
    ]


@router.get("/moodle/courses")
@limiter.limit("30/minute", key_func=user_or_address)
def list_moodle_courses(
    request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    try:
        return teacher_courses(db, current_user)
    except MOODLE_FAILURES as error:
        raise moodle_failure(error, db) from error


@router.post("/{course_id}/link-moodle")
def link_moodle_course(
    course_id: uuid.UUID,
    payload: LinkMoodleCourse,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    course = _get_owned_course(course_id, db, current_user)
    moodle_course_id = payload.moodle_course_id
    try:
        require_course_author(db, current_user, moodle_course_id)
    except MOODLE_FAILURES as error:
        raise moodle_failure(error, db) from error
    course.moodle_course_id = moodle_course_id
    db.commit()
    return {"moodle_course_id": moodle_course_id}


@router.delete("/{course_id}/link-moodle")
def unlink_moodle_course(
    course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Clears the link only -- never touches the Moodle side, since the
    teacher may have added content there beyond what this app manages."""
    course = _get_owned_course(course_id, db, current_user)
    course.moodle_course_id = None
    db.commit()
    return {"moodle_course_id": None}


@router.delete("/{course_id}")
def delete_course(
    course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Deletes the course and everything under it (documents, chunks,
    modules, quizzes, chat history) via ON DELETE CASCADE -- migration
    3d8f2a1c6e9b. Does not touch Moodle even if linked, same reasoning as
    unlink_moodle_course above.
    """
    course = _get_owned_course(course_id, db, current_user)
    check_study_references(db, db.scalars(select(Module.id).where(Module.course_id == course_id)))
    # A document whose pipeline never finished still has its upload; see delete_document.
    storage_keys = [upload_key(doc_id, filename) for doc_id, filename in
                    db.execute(select(Document.id, Document.filename).where(Document.course_id == course_id))]
    db.delete(course)
    commit_deletion(db)
    store = upload_store()
    for key in storage_keys:
        store.delete(key)
    return {"status": "deleted"}


@router.get("/{course_id}/moodle-sections")
def get_moodle_sections(
    course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    course = _get_owned_course(course_id, db, current_user)
    if course.moodle_course_id is None:
        raise HTTPException(status_code=400, detail="Course is not linked to a Moodle course yet")
    try:
        require_course_author(db, current_user, course.moodle_course_id)
        return moodle_client.get_course_sections(course.moodle_course_id)
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db, course) from e
