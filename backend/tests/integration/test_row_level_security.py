"""What the database itself enforces, whatever the application code does: connected as the
restricted role the requests use, a user context sees only that user's rows, no context
sees nothing, and nobody can change or add to another user's data."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.db import RequestSessionLocal, SessionLocal
from app.models import (
    ChatMessage,
    ChatRole,
    ChatSession,
    Chunk,
    ComponentType,
    Course,
    Document,
    DocumentStatus,
    Evaluation,
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
    Question,
    User,
)

# table -> predicate selecting the rows the owner made
TENANT_TABLES = {
    "courses": "id = :course",
    "documents": "id = :document",
    "chunks": "document_id = :document",
    "modules": "id = :module",
    "module_components": "module_id = :module",
    "questions": "component_id = :component",
    "evaluations": "module_id = :module",
    "chat_sessions": "id = :session",
    "chat_messages": "session_id = :session",
    "jobs": "document_id = :document",
}


@pytest.fixture(scope="module")
def world():
    db = SessionLocal()
    try:
        owner = User(email=f"rls-a-{uuid.uuid4().hex[:8]}@example.com", name="A", hashed_password="x", is_admin=False)
        stranger = User(email=f"rls-b-{uuid.uuid4().hex[:8]}@example.com", name="B", hashed_password="x", is_admin=False)
        db.add_all([owner, stranger])
        db.flush()
        course = Course(name="Course of A", owner_id=owner.id)
        db.add(course)
        db.flush()
        document = Document(course_id=course.id, filename="a.txt", status=DocumentStatus.embedded)
        db.add(document)
        db.flush()
        db.add(Chunk(document_id=document.id, course_id=course.id, chunk_index=0, content="x"))
        module = Module(document_id=document.id, course_id=course.id, title="M")
        db.add(module)
        db.flush()
        component = ModuleComponent(module_id=module.id, type=ComponentType.quiz, content={})
        db.add(component)
        db.add(Evaluation(module_id=module.id, criterion="c", score=1.0))
        session = ChatSession(course_id=course.id, title="t")
        db.add(session)
        db.flush()
        db.add(Question(component_id=component.id, stem="s", choices={"options": []}, answer_key="0"))
        db.add(ChatMessage(session_id=session.id, role=ChatRole.user, content="hi", citations=[]))
        db.add(Job(job_type=JobType.module_generation, document_id=document.id, step="generate", status=JobStatus.done))
        db.commit()
        ids = {
            "owner": owner.id, "stranger": stranger.id, "course": course.id, "document": document.id,
            "module": module.id, "component": component.id, "session": session.id,
        }
    finally:
        db.close()

    yield ids

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids["owner"], ids["stranger"]])).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _as(user_id):
    db = RequestSessionLocal()
    if user_id is not None:
        db.info["rls_user_id"] = user_id
    return db


def _ids(world):
    return {k: v for k, v in world.items() if k in {"course", "document", "module", "component", "session"}}


def test_the_requests_use_a_role_that_row_level_security_applies_to(world):
    db = _as(world["owner"])
    try:
        row = db.execute(text("select rolsuper, rolbypassrls from pg_roles where rolname = current_user")).one()
    finally:
        db.close()

    assert tuple(row) == (False, False)


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_the_owner_sees_their_rows_and_only_they_do(world, table):
    count = f"select count(*) from {table} where {TENANT_TABLES[table]}"
    seen = {}
    for who in ("owner", "stranger", None):
        db = _as(world[who] if who else None)
        try:
            seen[who] = db.execute(text(count), _ids(world)).scalar_one()
        finally:
            db.close()

    assert seen["owner"] >= 1
    assert seen["stranger"] == 0
    assert seen[None] == 0  # no user context: it fails closed


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_another_user_cannot_delete_the_rows_of_the_owner(world, table):
    db = _as(world["stranger"])
    try:
        removed = db.execute(text(f"delete from {table} where {TENANT_TABLES[table]}"), _ids(world)).rowcount
        db.rollback()
    finally:
        db.close()

    assert removed == 0


def test_another_user_cannot_change_or_take_over_a_course(world):
    db = _as(world["stranger"])
    try:
        renamed = db.execute(text("update courses set name = 'pwned' where id = :course"), _ids(world)).rowcount
        taken = db.execute(
            text("update courses set owner_id = :me where id = :course"), {**_ids(world), "me": world["stranger"]}
        ).rowcount
        db.rollback()
    finally:
        db.close()

    assert (renamed, taken) == (0, 0)


@pytest.mark.parametrize(
    "statement",
    [
        "insert into modules (id, document_id, course_id, title) values (gen_random_uuid(), :document, :course, 'x')",
        "insert into chat_sessions (id, course_id, title) values (gen_random_uuid(), :course, 'x')",
        "insert into documents (id, course_id, filename, status) values (gen_random_uuid(), :course, 'x', 'uploaded')",
    ],
    ids=["module", "chat-session", "document"],
)
def test_another_user_cannot_add_rows_to_the_owners_course(world, statement):
    db = _as(world["owner"])
    try:
        assert db.execute(text(statement), _ids(world)).rowcount == 1
    finally:
        db.rollback()
        db.close()

    db = _as(world["stranger"])
    try:
        with pytest.raises(ProgrammingError) as refused:
            db.execute(text(statement), _ids(world))
    finally:
        db.rollback()
        db.close()

    assert refused.value.orig.sqlstate == "42501"
    assert "row-level security" in str(refused.value.orig)


def test_the_restricted_role_cannot_create_tables(world):
    db = _as(world["owner"])
    try:
        with pytest.raises(ProgrammingError):
            db.execute(text("create table probe_should_not_exist (x int)"))
    finally:
        db.rollback()
        db.close()
