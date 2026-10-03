import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth.rate_limit import limiter, user_or_address
from app.db import get_db
from app.models import Course, MoodleCourseClaim, User
from app.moodle import client as moodle_client
from app.moodle.accounts import require_moodle_user_id
from app.moodle.client import MoodleError
from app.moodle.errors import MOODLE_FAILURES, moodle_failure

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


@router.get("/moodle/search")
@limiter.limit("30/minute", key_func=user_or_address)
def search_moodle_courses(
    request: Request, q: str = Query(min_length=1, max_length=100), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Search existing Moodle courses (e.g. a teacher's own "CO3061") so a
    course can be linked instead of always auto-creating a new one.

    Excludes Moodle courses already linked to a course here -- the mapping
    is 1-1, so an already-linked course must not be offered again for a
    different course.
    """
    try:
        results = moodle_client.search_courses(q)
    except MoodleError as e:
        raise HTTPException(status_code=502, detail=f"Moodle search failed: {e}") from e

    # What is left out is what this user could not link anyway: their own links (the
    # mapping is 1-1) and every course another user has claimed, whether or not it is
    # still linked. Nothing tells them which of the two it was, or that it was there.
    linked_ids = set(db.scalars(select(Course.moodle_course_id).where(Course.moodle_course_id.isnot(None))))
    claimed_by_others = set(db.scalars(
        select(MoodleCourseClaim.moodle_course_id).where(
            MoodleCourseClaim.moodle_course_id.in_([r["id"] for r in results]),
            MoodleCourseClaim.owner_id != current_user.id,
        )
    ))
    return [r for r in results if r["id"] not in linked_ids and r["id"] not in claimed_by_others]


@router.post("/{course_id}/link-moodle")
def link_moodle_course(
    course_id: uuid.UUID,
    payload: LinkMoodleCourse,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    course = _get_owned_course(course_id, db, current_user)
    moodle_course_id = payload.moodle_course_id
    # The same answer whether the course is somebody else's or does not exist at all, so
    # a stranger cannot tell which ids are taken.
    unavailable = HTTPException(status_code=404, detail="Không tìm thấy khóa học Moodle này")
    try:
        exists = moodle_client.get_course(moodle_course_id) is not None
    except MoodleError as e:
        raise HTTPException(status_code=502, detail=f"Moodle course lookup failed: {e}") from e
    if not exists:
        raise unavailable

    claim = db.get(MoodleCourseClaim, moodle_course_id)
    if claim is not None and claim.owner_id != current_user.id:
        raise unavailable
    if claim is None:
        db.add(MoodleCourseClaim(moodle_course_id=moodle_course_id, owner_id=current_user.id))
    course.moodle_course_id = moodle_course_id
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        # Either another course of this user has it, or somebody else claimed it while
        # this request was running: the claim says which.
        claim = db.get(MoodleCourseClaim, moodle_course_id)
        if claim is not None and claim.owner_id != current_user.id:
            raise unavailable from e
        raise HTTPException(
            status_code=409, detail="This Moodle course is already linked to another course here"
        ) from e
    return {"moodle_course_id": course.moodle_course_id}


@router.post("/{course_id}/create-moodle")
@limiter.limit("5/minute", key_func=user_or_address)  # each call creates a real Moodle course
def create_moodle_course(
    request: Request, course_id: uuid.UUID, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Explicitly create a brand new Moodle course and link it -- for
    courses that don't already exist in Moodle (see /moodle/search for
    linking to one that does)."""
    course = _get_owned_course(course_id, db, current_user)
    if course.moodle_course_id is not None:
        raise HTTPException(status_code=400, detail="Course already linked to a Moodle course")
    # Before anything is created: the teacher is the lecturer's own Moodle account, found by
    # email. Without one nothing is pushed, so no course is left behind in Moodle.
    try:
        teacher_id = require_moodle_user_id(current_user)
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db) from e
    try:
        course.moodle_course_id = moodle_client.create_course(course.name)
    except MoodleError as e:
        raise HTTPException(status_code=502, detail=f"Moodle course creation failed: {e}") from e
    db.add(MoodleCourseClaim(moodle_course_id=course.moodle_course_id, owner_id=current_user.id))
    # Commit the link before enrolling: the Moodle course exists now, so if
    # enrolling fails a retry must not create a second, orphaned course.
    db.commit()
    try:
        moodle_client.enrol_as_teacher(course.moodle_course_id, teacher_id)
    except MoodleError as e:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Moodle course {course.moodle_course_id} was created and linked, "
                f"but enrolling you as its teacher failed: {e}"
            ),
        ) from e
    return {"moodle_course_id": course.moodle_course_id}


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
    db.delete(course)
    db.commit()
    return {"status": "deleted"}


@router.get("/{course_id}/moodle-sections")
def get_moodle_sections(
    course_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    course = _get_owned_course(course_id, db, current_user)
    if course.moodle_course_id is None:
        raise HTTPException(status_code=400, detail="Course is not linked to a Moodle course yet")
    try:
        return moodle_client.get_course_sections(course.moodle_course_id)
    except MOODLE_FAILURES as e:
        raise moodle_failure(e, db, course) from e
