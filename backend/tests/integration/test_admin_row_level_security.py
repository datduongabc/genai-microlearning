"""What the database lets an admin reach, tested with the role the application runs as.

An admin owns no course, so row level security shows them none of the lecturers' data. The only
doors are the view admin_failed_jobs (failed jobs, with file name, course name and owner, and no
content) and the function retry_failed_job. These tests go to the database directly, so they hold
even if an endpoint were written carelessly."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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
    Job,
    JobStatus,
    JobType,
    Module,
    ModuleComponent,
    Question,
)
from tests.auth_helpers import make_account


@pytest.fixture(scope="module")
def world():
    owner = make_account(f"rls-owner-{uuid.uuid4().hex[:8]}@example.com")
    admin = make_account(f"rls-admin-{uuid.uuid4().hex[:8]}@example.com", is_admin=True)
    disabled_admin = make_account(f"rls-off-{uuid.uuid4().hex[:8]}@example.com", is_admin=True)
    with SessionLocal() as db:
        db.execute(text("UPDATE users SET is_active = false WHERE id = :id"), {"id": disabled_admin.id})
        course = Course(name="Môn của A", owner_id=owner.id)
        db.add(course)
        db.flush()
        document = Document(course_id=course.id, filename="a.pdf", raw_text="secret text", status=DocumentStatus.failed)
        db.add(document)
        db.flush()
        db.add(Chunk(document_id=document.id, course_id=course.id, chunk_index=0, content="secret chunk"))
        module = Module(document_id=document.id, course_id=course.id, title="Module of A")
        db.add(module)
        db.flush()
        component = ModuleComponent(module_id=module.id, type=ComponentType.quiz, content={})
        db.add(component)
        db.flush()
        db.add(Question(component_id=component.id, stem="secret", choices={"options": ["A", "B"]}, answer_key="0"))
        session = ChatSession(course_id=course.id, title="chat")
        db.add(session)
        db.flush()
        db.add(ChatMessage(session_id=session.id, role=ChatRole.user, content="secret chat", citations=[]))
        failed = Job(job_type=JobType.document_pipeline, document_id=document.id, step="extract",
                     status=JobStatus.failed, last_error="boom", payload={"units": ["secret payload"]})
        queued = Job(job_type=JobType.module_generation, document_id=document.id, step="generate", status=JobStatus.queued)
        db.add_all([failed, queued])
        db.commit()
        course_id = course.id
        ids = {
            "owner": owner.id, "admin": admin.id, "disabled_admin": disabled_admin.id, "document": document.id,
            "failed": failed.id, "queued": queued.id,
        }
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == course_id).delete(synchronize_session=False)
        db.commit()


def _as(user_id):
    """A session of the role the application runs as, acting for `user_id` (None: nobody)."""
    db = RequestSessionLocal()
    if user_id is not None:
        db.execute(text(f"SET LOCAL app.current_user_id = '{user_id}'"))
    return db


TABLES = ("courses", "documents", "chunks", "modules", "module_components",
          "questions", "evaluations", "chat_sessions", "chat_messages", "jobs")


@pytest.mark.parametrize("table", TABLES)
def test_an_admin_sees_no_row_of_any_lecturer_table(world, table):
    with _as(world["admin"]) as db:
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_the_lecturer_still_sees_their_own_rows(world):
    with _as(world["owner"]) as db:
        assert db.execute(text("SELECT count(*) FROM documents")).scalar() == 1
        assert db.execute(text("SELECT count(*) FROM jobs")).scalar() == 2


def test_the_view_gives_an_admin_the_failed_jobs_with_names_and_nothing_else(world):
    with _as(world["admin"]) as db:
        result = db.execute(text("SELECT * FROM admin_failed_jobs WHERE id = :id"), {"id": world["failed"]})
        row = result.mappings().one()
        columns = set(result.keys())

    assert row["document_filename"] == "a.pdf" and row["course_name"] == "Môn của A"
    assert row["owner_email"].startswith("rls-owner-")
    assert not {"payload", "raw_text", "content", "document_id", "owner_id"} & columns


def test_the_view_shows_only_failed_jobs(world):
    with _as(world["admin"]) as db:
        ids = {r.id for r in db.execute(text("SELECT id FROM admin_failed_jobs"))}

    assert world["failed"] in ids and world["queued"] not in ids


@pytest.mark.parametrize("who", ["owner", "disabled_admin", None])
def test_the_view_is_empty_for_anyone_who_is_not_an_active_admin(world, who):
    with _as(world[who] if who else None) as db:
        assert db.execute(text("SELECT count(*) FROM admin_failed_jobs")).scalar() == 0


def test_an_admin_cannot_change_anything_with_plain_statements(world):
    with _as(world["admin"]) as db:
        for statement in (
            "UPDATE jobs SET status = 'queued'",
            "UPDATE documents SET status = 'embedded'",
            "UPDATE courses SET name = 'pwned'",
            "DELETE FROM jobs",
            "DELETE FROM documents",
        ):
            assert db.execute(text(statement)).rowcount == 0, statement
    with SessionLocal() as db:
        assert db.get(Job, world["failed"]).status == JobStatus.failed


def test_the_course_count_view_gives_an_admin_only_a_number_per_owner(world):
    with _as(world["admin"]) as db:
        result = db.execute(text("SELECT * FROM admin_course_counts WHERE owner_id = :id"), {"id": world["owner"]})
        row = result.mappings().one()
        columns = set(result.keys())

    assert row["course_count"] == 1 and columns == {"owner_id", "course_count"}


@pytest.mark.parametrize("who", ["owner", "disabled_admin", None])
def test_the_course_count_view_is_empty_for_anyone_who_is_not_an_active_admin(world, who):
    with _as(world[who] if who else None) as db:
        assert db.execute(text("SELECT count(*) FROM admin_course_counts")).scalar() == 0


def test_the_internal_view_is_not_open_to_the_application_role(world):
    with _as(world["admin"]) as db, pytest.raises(DBAPIError):
        db.execute(text("SELECT * FROM open_failed_jobs"))


def test_retry_works_for_an_admin_on_any_job_and_for_the_owner_on_their_own(world):
    with _as(world["admin"]) as db:
        assert db.execute(text("SELECT retry_failed_job(:id)"), {"id": world["failed"]}).scalar() is True
        db.commit()
    with SessionLocal() as db:
        job = db.get(Job, world["failed"])
        assert job.status == JobStatus.queued and job.attempts == 0
        assert db.get(Document, world["document"]).status == DocumentStatus.processing
        job.status = JobStatus.failed
        db.get(Document, world["document"]).status = DocumentStatus.failed
        db.commit()
    with _as(world["owner"]) as db:
        assert db.execute(text("SELECT retry_failed_job(:id)"), {"id": world["failed"]}).scalar() is True
        db.commit()
    with SessionLocal() as db:
        db.get(Job, world["failed"]).status = JobStatus.failed
        db.commit()


@pytest.mark.parametrize("who", ["disabled_admin", None])
def test_retry_does_nothing_for_a_stranger_or_a_disabled_admin(world, who):
    stranger = make_account(f"rls-stranger-{uuid.uuid4().hex[:8]}@example.com").id
    with _as(world[who] if who else stranger) as db:
        assert db.execute(text("SELECT retry_failed_job(:id)"), {"id": world["failed"]}).scalar() is False
        db.commit()
    with SessionLocal() as db:
        assert db.get(Job, world["failed"]).status == JobStatus.failed


def test_retry_does_nothing_for_a_job_that_is_not_failed(world):
    with _as(world["admin"]) as db:
        assert db.execute(text("SELECT retry_failed_job(:id)"), {"id": world["queued"]}).scalar() is False
        assert db.execute(text("SELECT retry_failed_job(:id)"), {"id": uuid.uuid4()}).scalar() is False
