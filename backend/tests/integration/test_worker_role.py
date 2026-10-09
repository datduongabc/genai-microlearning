"""The background worker connects as its own role, not as the owner of the database.

The worker has to see every user's documents and jobs, so it is exempt from row level
security, but that is all it is given: it cannot read passwords, tokens, the
authentication trail or chats, cannot change who owns what, and cannot change the schema.
A flaw in anything the worker runs (a document parser fed a hostile file, say) then does not
hand over the database. The web process gets no such role either way: it serves requests as
app_runtime and carries no owner credentials in production."""

import pathlib
import re
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from app.config import settings
from app.db import SessionLocal, WorkerSessionLocal
from app.models import Course, Document, DocumentStatus, User


@pytest.fixture(scope="module")
def world():
    db = SessionLocal()
    try:
        owners = [
            User(email=f"worker-{i}-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
            for i in range(2)
        ]
        db.add_all(owners)
        db.flush()
        courses = [Course(name=f"C{i}", owner_id=o.id) for i, o in enumerate(owners)]
        db.add_all(courses)
        db.flush()
        documents = [
            Document(course_id=c.id, filename=f"d{i}.txt", status=DocumentStatus.uploaded) for i, c in enumerate(courses)
        ]
        db.add_all(documents)
        db.commit()
        ids = {"users": [o.id for o in owners], "documents": [d.id for d in documents]}
    finally:
        db.close()

    yield ids

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.owner_id.in_(ids["users"])).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids["users"])).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _worker():
    return WorkerSessionLocal()


def test_the_worker_is_neither_a_superuser_nor_the_owner_but_is_exempt_from_row_security():
    db = _worker()
    try:
        row = db.execute(text(
            "select current_user, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
            "from pg_roles where rolname = current_user"
        )).one()
    finally:
        db.close()

    assert row.current_user == "app_worker"
    assert (row.rolsuper, row.rolcreatedb, row.rolcreaterole) == (False, False, False)
    assert row.rolbypassrls is True


def test_the_worker_sees_the_documents_of_every_user(world):
    db = _worker()
    try:
        seen = {d for (d,) in db.execute(
            text("select id from documents where id = any(:ids)"), {"ids": world["documents"]}
        )}
    finally:
        db.close()

    assert seen == set(world["documents"])


def test_the_worker_can_do_what_the_pipelines_do_with_jobs_and_documents(world):
    db = _worker()
    try:
        doc = world["documents"][0]
        db.execute(text("update documents set status = 'processing' where id = :d"), {"d": doc})
        job = db.execute(
            text("insert into jobs (id, job_type, document_id, step, status, payload, attempts, max_attempts) "
                 "values (gen_random_uuid(), 'document_pipeline', :d, 'extract', 'queued', '{}', 0, 3) returning id"),
            {"d": doc},
        ).scalar_one()
        db.execute(text("update jobs set status = 'running' where id = :j"), {"j": job})
        db.execute(text("delete from jobs where id = :j"), {"j": job})
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize(
    "statement",
    [
        "select hashed_password from users",
        "select email from users",
        "insert into users (id, email, name, hashed_password, role) values (gen_random_uuid(), 'x@y.z', 'x', 'x', 'teacher')",
        "update courses set owner_id = owner_id",
        "select token_hash from refresh_tokens",
        "update refresh_tokens set revoked_at = now()",
        "select * from chat_messages",
        "select * from chat_sessions",
        "create table worker_should_not_create (x int)",
        "drop table jobs",
        "alter table documents add column worker_probe int",
        "create role worker_probe_role",
    ],
    ids=lambda s: s[:60],
)
def test_the_worker_is_refused_everything_outside_its_job(statement):
    db = _worker()
    try:
        with pytest.raises(ProgrammingError):
            db.execute(text(statement))
    finally:
        db.rollback()
        db.close()


def test_the_worker_can_still_clear_out_what_the_periodic_clean_up_needs():
    db = _worker()
    try:
        # A DELETE with a condition reads the columns in it: only those are readable.
        db.execute(text("delete from refresh_tokens where expires_at < now() - interval '100 years'"))
        db.execute(text("delete from refresh_tokens where revoked_at < now() - interval '100 years'"))
        db.commit()
    finally:
        db.close()


def test_a_worker_url_of_its_own_is_what_the_worker_session_uses():
    engine = create_engine(settings.app_worker_database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("select current_user")).scalar_one() == "app_worker"
    finally:
        engine.dispose()


def test_nothing_the_application_runs_uses_the_owner_session():
    """The owner session is for migrations, tests and scripts. Any other use puts the
    owner's rights back into the process that serves requests or runs the worker."""
    app_dir = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        if path.name == "db.py":
            continue
        if re.search(r"(?<![A-Za-z])SessionLocal\b", path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(app_dir)))

    assert offenders == []
