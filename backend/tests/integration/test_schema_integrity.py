"""Properties the database schema itself must guarantee, independent of how the
application code happens to use it."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.db import Base, SessionLocal
from app.models import (
    Chunk,
    ComponentType,
    Course,
    Document,
    DocumentStatus,
    Module,
    ModuleComponent,
    User,
)

APP_TABLES = sorted(Base.metadata.tables)


def test_every_foreign_key_has_a_supporting_index():
    """Postgres does not index FK columns on its own. Without one, every
    ON DELETE CASCADE and every RLS policy that filters on the column scans
    the whole child table."""
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                select c.conrelid::regclass::text as tbl, a.attname as col
                from pg_constraint c
                join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
                where c.contype = 'f'
                  and c.connamespace = 'public'::regnamespace
                  and c.conrelid::regclass::text = any(:tables)
                  and not exists (
                      select 1 from pg_index i
                      where i.indrelid = c.conrelid and i.indkey[0] = c.conkey[1]
                  )
                order by 1, 2
                """
            ),
            {"tables": APP_TABLES},
        ).all()
    finally:
        db.close()

    assert [f"{r.tbl}.{r.col}" for r in rows] == []


def test_every_timestamp_column_is_timezone_aware():
    """A naive timestamp silently depends on the server/session time zone and
    is parsed as local time by browsers; mixing naive and aware columns has
    already caused a comparison bug once."""
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                select table_name, column_name from information_schema.columns
                where table_schema = 'public'
                  and table_name = any(:tables)
                  and data_type = 'timestamp without time zone'
                order by 1, 2
                """
            ),
            {"tables": APP_TABLES},
        ).all()
    finally:
        db.close()

    assert [f"{r.table_name}.{r.column_name}" for r in rows] == []


@pytest.fixture
def world():
    """A teacher, two courses (A, B) and one document in course A."""
    db = SessionLocal()
    try:
        user = User(
            email=f"schema-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False
        )
        db.add(user)
        db.flush()
        course_a = Course(name="Schema A", owner_id=user.id)
        course_b = Course(name="Schema B", owner_id=user.id)
        db.add_all([course_a, course_b])
        db.flush()
        doc = Document(course_id=course_a.id, filename="a.txt", status=DocumentStatus.embedded)
        db.add(doc)
        db.commit()
        ids = {"user": user.id, "course_a": course_a.id, "course_b": course_b.id, "doc": doc.id}
    finally:
        db.close()

    yield ids

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id.in_([ids["course_a"], ids["course_b"]])).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _commit(row) -> None:
    db = SessionLocal()
    try:
        db.add(row)
        db.commit()
    finally:
        db.close()


def test_chunk_of_the_documents_own_course_is_accepted(world):
    _commit(Chunk(document_id=world["doc"], course_id=world["course_a"], chunk_index=0, content="x"))


def test_chunk_cannot_claim_a_different_course_than_its_document(world):
    with pytest.raises(IntegrityError):
        _commit(Chunk(document_id=world["doc"], course_id=world["course_b"], chunk_index=0, content="x"))


def test_module_of_the_documents_own_course_is_accepted(world):
    _commit(Module(document_id=world["doc"], course_id=world["course_a"], title="ok"))


def test_module_cannot_claim_a_different_course_than_its_document(world):
    with pytest.raises(IntegrityError):
        _commit(Module(document_id=world["doc"], course_id=world["course_b"], title="bad"))


def test_a_module_has_at_most_one_component_of_each_type(world):
    db = SessionLocal()
    try:
        module = Module(document_id=world["doc"], course_id=world["course_a"], title="m")
        db.add(module)
        db.flush()
        db.add(ModuleComponent(module_id=module.id, type=ComponentType.quiz, content={}))
        db.add(ModuleComponent(module_id=module.id, type=ComponentType.content, content={}))
        db.commit()  # different types are fine

        db.add(ModuleComponent(module_id=module.id, type=ComponentType.quiz, content={}))
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()
