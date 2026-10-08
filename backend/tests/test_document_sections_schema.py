"""What the database itself guarantees about textbook sections."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import RequestSessionLocal, SessionLocal
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


def _make_user(db) -> User:
    user = User(
        email=f"sec-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False
    )
    db.add(user)
    db.flush()
    return user


def _make_textbook(db, course_id) -> tuple[Document, DocumentSection]:
    doc = Document(
        course_id=course_id, filename="book.pdf", status=DocumentStatus.embedded, kind=DocumentKind.textbook
    )
    db.add(doc)
    db.flush()
    section = DocumentSection(
        document_id=doc.id, title="Chapter 1", level=1, position=0, first_chunk_index=0, last_chunk_index=3
    )
    db.add(section)
    db.flush()
    return doc, section


@pytest.fixture
def world():
    db = SessionLocal()
    try:
        user = _make_user(db)
        course = Course(name="Sections schema", owner_id=user.id)
        db.add(course)
        db.flush()
        doc_a, section_a = _make_textbook(db, course.id)
        doc_b, section_b = _make_textbook(db, course.id)
        lecture = Document(course_id=course.id, filename="slides.pdf", status=DocumentStatus.embedded)
        db.add(lecture)
        db.commit()
        ids = {
            "user": user.id,
            "course": course.id,
            "doc_a": doc_a.id,
            "section_a": section_a.id,
            "doc_b": doc_b.id,
            "section_b": section_b.id,
            "lecture": lecture.id,
        }
    finally:
        db.close()

    yield ids

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _add_module(db, world, document_id, section_id) -> None:
    db.add(Module(document_id=document_id, course_id=world["course"], title="m", section_id=section_id))
    db.commit()


def test_a_document_is_a_lecture_unless_it_says_otherwise(world):
    db = SessionLocal()
    try:
        assert db.get(Document, world["lecture"]).kind == DocumentKind.lecture
        assert db.get(Document, world["doc_a"]).kind == DocumentKind.textbook
    finally:
        db.close()


def test_a_module_can_belong_to_a_section_of_its_own_document(world):
    db = SessionLocal()
    try:
        _add_module(db, world, world["doc_a"], world["section_a"])
    finally:
        db.close()


def test_a_module_cannot_belong_to_a_section_of_another_document(world):
    db = SessionLocal()
    try:
        with pytest.raises(IntegrityError):
            _add_module(db, world, world["doc_a"], world["section_b"])
    finally:
        db.rollback()
        db.close()


def test_a_job_can_target_a_section_of_its_own_document(world):
    db = SessionLocal()
    try:
        db.add(
            Job(
                job_type=JobType.module_generation,
                step="generate",
                status=JobStatus.done,
                document_id=world["doc_a"],
                section_id=world["section_a"],
            )
        )
        db.commit()
    finally:
        db.close()


def test_a_job_cannot_target_a_section_of_another_document(world):
    db = SessionLocal()
    try:
        db.add(
            Job(
                job_type=JobType.module_generation,
                step="generate",
                status=JobStatus.done,
                document_id=world["doc_a"],
                section_id=world["section_b"],
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize(
    "fields",
    [
        {"position": 0, "first_chunk_index": 0, "last_chunk_index": 0},  # position already taken
        {"position": 5, "first_chunk_index": 4, "last_chunk_index": 2},  # range runs backwards
        {"position": 6, "first_chunk_index": 0, "last_chunk_index": 1, "level": 4},  # deeper than 3 levels
    ],
    ids=["duplicate-position", "reversed-chunk-range", "level-too-deep"],
)
def test_invalid_sections_are_rejected(world, fields):
    row = {"document_id": world["doc_a"], "title": "bad", "level": 1, **fields}
    db = SessionLocal()
    try:
        db.add(DocumentSection(**row))
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_deleting_a_document_removes_its_sections_and_their_modules(world):
    db = SessionLocal()
    try:
        _add_module(db, world, world["doc_a"], world["section_a"])
        db.query(Document).filter(Document.id == world["doc_a"]).delete(synchronize_session=False)
        db.commit()

        assert db.query(DocumentSection).filter(DocumentSection.id == world["section_a"]).count() == 0
        assert db.query(Module).filter(Module.section_id == world["section_a"]).count() == 0
    finally:
        db.close()


def test_a_user_only_sees_the_sections_of_their_own_courses(world):
    db = SessionLocal()
    try:
        other = _make_user(db)
        other_course = Course(name="Someone else", owner_id=other.id)
        db.add(other_course)
        db.flush()
        _doc, other_section = _make_textbook(db, other_course.id)
        db.commit()
        other_user_id, other_course_id, other_section_id = other.id, other_course.id, other_section.id
    finally:
        db.close()

    try:
        request_db = RequestSessionLocal()
        request_db.info["rls_user_id"] = world["user"]
        try:
            visible = {s.id for s in request_db.query(DocumentSection).all()}
        finally:
            request_db.close()

        assert world["section_a"] in visible
        assert other_section_id not in visible
    finally:
        db = SessionLocal()
        try:
            db.query(Course).filter(Course.id == other_course_id).delete(synchronize_session=False)
            db.query(User).filter(User.id == other_user_id).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
