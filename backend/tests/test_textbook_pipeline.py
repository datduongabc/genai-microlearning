"""A textbook run through the real extract -> chunk -> embed steps (local
embedding model, no external API) ends up with its outline in the database."""

import uuid

import pymupdf
import pytest

from app.db import SessionLocal
from app.ingestion.document_pipeline import run_document_pipeline_step
from app.jobs.queue import enqueue_job
from app.models import (
    Chunk,
    Course,
    Document,
    DocumentKind,
    DocumentSection,
    DocumentStatus,
    Job,
    JobType,
    User,
    UserRole,
)


def _book_pdf(path, *, bookmarks: bool) -> None:
    doc = pymupdf.open()
    for page_number in range(1, 7):
        page = doc.new_page()
        for line in range(14):
            page.insert_text(
                (60, 60 + line * 17),
                f"Page {page_number} line {line}: some plain text about the topic of this page.",
                fontsize=11,
            )
    if bookmarks:
        doc.set_toc([[1, "Chapter One", 1], [2, "Section 1.1", 2], [1, "Chapter Two", 4]])
    doc.save(str(path))
    doc.close()


@pytest.fixture
def make_document(tmp_path):
    created: list[tuple[uuid.UUID, uuid.UUID]] = []

    def _make(kind: DocumentKind, *, bookmarks: bool = True):
        path = tmp_path / f"{uuid.uuid4().hex}.pdf"
        _book_pdf(path, bookmarks=bookmarks)
        db = SessionLocal()
        try:
            user = User(
                email=f"book-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", role=UserRole.teacher
            )
            db.add(user)
            db.flush()
            course = Course(name="Textbook pipeline", owner_id=user.id)
            db.add(course)
            db.flush()
            doc = Document(course_id=course.id, filename="book.pdf", status=DocumentStatus.uploaded, kind=kind)
            db.add(doc)
            db.flush()
            job = enqueue_job(
                db,
                job_type=JobType.document_pipeline,
                step="extract",
                document_id=doc.id,
                payload={"storage_path": str(path)},
            )
            db.commit()
            created.append((user.id, course.id))
            return doc.id, job.id
        finally:
            db.close()

    yield _make

    # Deleting the course cascades to documents and jobs, so no queued job is
    # left behind for the job-queue tests.
    db = SessionLocal()
    try:
        for user_id, course_id in created:
            db.query(Course).filter(Course.id == course_id).delete(synchronize_session=False)
            db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _run_pipeline(job_id) -> None:
    for _step in ("extract", "chunk", "embed"):
        db = SessionLocal()
        try:
            run_document_pipeline_step(db.get(Job, job_id))
        finally:
            db.close()


def _sections_and_chunk_count(document_id):
    db = SessionLocal()
    try:
        sections = (
            db.query(DocumentSection)
            .filter(DocumentSection.document_id == document_id)
            .order_by(DocumentSection.position)
            .all()
        )
        chunk_count = db.query(Chunk).filter(Chunk.document_id == document_id).count()
        status = db.get(Document, document_id).status
        return sections, chunk_count, status
    finally:
        db.close()


def test_a_textbook_with_bookmarks_gets_its_outline_stored(make_document):
    doc_id, job_id = make_document(DocumentKind.textbook)

    _run_pipeline(job_id)

    sections, chunk_count, status = _sections_and_chunk_count(doc_id)
    assert status == DocumentStatus.embedded
    assert [(s.title, s.level) for s in sections] == [("Chapter One", 1), ("Section 1.1", 2), ("Chapter Two", 1)]
    chapter_one, section_one, chapter_two = sections
    assert chapter_one.parent_id is None
    assert section_one.parent_id == chapter_one.id
    assert chapter_two.parent_id is None
    assert (chapter_one.page_start, chapter_two.page_start) == (1, 4)
    for section in sections:
        assert 0 <= section.first_chunk_index <= section.last_chunk_index < chunk_count
    # a parent covers its child, and sibling chapters do not overlap
    assert chapter_one.first_chunk_index <= section_one.first_chunk_index
    assert section_one.last_chunk_index <= chapter_one.last_chunk_index
    assert chapter_one.last_chunk_index < chapter_two.first_chunk_index


def test_a_lecture_never_gets_an_outline(make_document):
    doc_id, job_id = make_document(DocumentKind.lecture)

    _run_pipeline(job_id)

    sections, _chunk_count, status = _sections_and_chunk_count(doc_id)
    assert sections == []
    assert status == DocumentStatus.embedded


def test_a_textbook_with_no_detectable_structure_is_still_processed(make_document):
    doc_id, job_id = make_document(DocumentKind.textbook, bookmarks=False)

    _run_pipeline(job_id)

    sections, chunk_count, status = _sections_and_chunk_count(doc_id)
    assert sections == []
    assert chunk_count > 0
    assert status == DocumentStatus.embedded
