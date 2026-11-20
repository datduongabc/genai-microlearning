"""What the database enforces for course members (migration 0027), connected as the role the requests use,
whatever the application code does: a member reads the course, its documents and chunks, and their own chats;
nothing else of the course, and nothing they can change. The owner does not read a member's chats."""

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
    CourseMember,
    Document,
    DocumentStatus,
    Evaluation,
    Job,
    JobStatus,
    JobType,
    LtiContext,
    LtiPlatform,
    Module,
    ModuleComponent,
    User,
)


@pytest.fixture(scope="module")
def world():
    with SessionLocal() as db:
        people = {name: User(email=f"member-{name}-{uuid.uuid4().hex[:8]}@example.com", name=name, hashed_password="x")
                  for name in ("owner", "member", "stranger")}
        db.add_all(people.values())
        db.flush()
        course = Course(name="Course", owner_id=people["owner"].id)
        unlinked = Course(name="Not in any LMS", owner_id=people["owner"].id)
        db.add_all([course, unlinked])
        db.flush()
        platform = LtiPlatform(issuer=f"https://lms-{uuid.uuid4().hex[:6]}.example", client_id="c",
                               auth_login_url="https://lms.example/auth", jwks_url="https://lms.example/certs")
        db.add(platform)
        db.flush()
        db.add(LtiContext(platform_id=platform.id, deployment_id="d", context_id="ctx", course_id=course.id))
        document = Document(course_id=course.id, filename="notes.pdf", status=DocumentStatus.embedded)
        db.add(document)
        db.flush()
        db.add(Chunk(document_id=document.id, course_id=course.id, chunk_index=0, content="material"))
        module = Module(document_id=document.id, course_id=course.id, title="Draft")
        db.add(module)
        db.flush()
        db.add(ModuleComponent(module_id=module.id, type=ComponentType.content, content={}))
        db.add(Evaluation(module_id=module.id, criterion="c", score=1.0))
        db.add(Job(job_type=JobType.document_pipeline, document_id=document.id, step="extract", status=JobStatus.done))
        owner_chat = ChatSession(course_id=course.id, user_id=people["owner"].id, title="owner's")
        member_chat = ChatSession(course_id=course.id, user_id=people["member"].id, title="member's")
        db.add_all([owner_chat, member_chat])
        db.flush()
        db.add(ChatMessage(session_id=owner_chat.id, role=ChatRole.user, content="hi", citations=[]))
        db.add(ChatMessage(session_id=member_chat.id, role=ChatRole.user, content="hello", citations=[]))
        db.add(CourseMember(course_id=course.id, user_id=people["member"].id))
        db.commit()
        ids = {**{k: u.id for k, u in people.items()}, "course": course.id, "unlinked": unlinked.id,
               "document": document.id, "module": module.id, "owner_chat": owner_chat.id,
               "member_chat": member_chat.id, "platform": platform.id}
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id.in_([ids["course"], ids["unlinked"]])).delete(synchronize_session=False)
        db.query(LtiPlatform).filter(LtiPlatform.id == ids["platform"]).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids["owner"], ids["member"], ids["stranger"]])).delete(synchronize_session=False)
        db.commit()


def _count(user_id, sql, params):
    db = RequestSessionLocal()
    db.info["rls_user_id"] = user_id
    try:
        return db.execute(text(sql), params).scalar_one()
    finally:
        db.close()


READS = {
    "courses": ("select count(*) from courses where id = :course", 1),
    "documents": ("select count(*) from documents where course_id = :course", 1),
    "chunks": ("select count(*) from chunks where course_id = :course", 1),
    "modules": ("select count(*) from modules where course_id = :course", 0),
    "module_components": ("select count(*) from module_components where module_id = :module", 0),
    "evaluations": ("select count(*) from evaluations where module_id = :module", 0),
    "jobs": ("select count(*) from jobs where document_id = :document", 0),
    "the owner's chat": ("select count(*) from chat_sessions where id = :owner_chat", 0),
    "the owner's messages": ("select count(*) from chat_messages where session_id = :owner_chat", 0),
    "their own chat": ("select count(*) from chat_sessions where id = :member_chat", 1),
    "their own messages": ("select count(*) from chat_messages where session_id = :member_chat", 1),
}


@pytest.mark.parametrize("what", READS)
def test_a_member_reads_the_material_and_their_chat_only(world, what):
    sql, expected = READS[what]
    assert _count(world["member"], sql, world) == expected


def test_the_owner_still_sees_all_of_their_course_but_not_the_members_chat(world):
    owner = world["owner"]
    for sql in ("select count(*) from modules where course_id = :course",
                "select count(*) from jobs where document_id = :document",
                "select count(*) from chat_sessions where id = :owner_chat"):
        assert _count(owner, sql, world) == 1
    assert _count(owner, "select count(*) from chat_sessions where id = :member_chat", world) == 0


@pytest.mark.parametrize("statement", [
    "update courses set name = 'pwned' where id = :course",
    "update courses set owner_id = :member where id = :course",
    "delete from courses where id = :course",
    "update documents set filename = 'pwned' where course_id = :course",
    "delete from documents where course_id = :course",
    "update chunks set content = 'pwned' where course_id = :course",
    "delete from chunks where course_id = :course",
    "update chat_sessions set title = 'pwned' where id = :owner_chat",
])
def test_a_member_changes_nothing_of_the_course(world, statement):
    db = RequestSessionLocal()
    db.info["rls_user_id"] = world["member"]
    try:
        assert db.execute(text(statement), world).rowcount == 0
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize("statement", [
    "insert into documents (id, course_id, filename, status) values (gen_random_uuid(), :course, 'x', 'uploaded')",
    ("insert into chunks (id, document_id, course_id, chunk_index, content) "
     "values (gen_random_uuid(), :document, :course, 99, 'x')"),
    "insert into modules (id, document_id, course_id, title) values (gen_random_uuid(), :document, :course, 'x')",
    # A chat in someone else's name.
    "insert into chat_sessions (id, course_id, user_id, title) values (gen_random_uuid(), :course, :owner, 'x')",
    # Membership for someone else, or in a course no LMS context opens.
    "insert into course_members (course_id, user_id) values (:course, :stranger)",
    "insert into course_members (course_id, user_id) values (:unlinked, :member)",
])
def test_a_member_adds_nothing_but_their_own_chat(world, statement):
    db = RequestSessionLocal()
    db.info["rls_user_id"] = world["member"]
    try:
        with pytest.raises(ProgrammingError) as refused:
            db.execute(text(statement), world)
    finally:
        db.rollback()
        db.close()
    assert refused.value.orig.sqlstate == "42501"


def test_a_member_starts_a_chat_of_their_own(world):
    db = RequestSessionLocal()
    db.info["rls_user_id"] = world["member"]
    try:
        added = db.execute(text("insert into chat_sessions (id, course_id, user_id, title) "
                                "values (gen_random_uuid(), :course, :member, 'x')"), world).rowcount
    finally:
        db.rollback()
        db.close()
    assert added == 1


def test_a_stranger_sees_nothing_and_cannot_join_a_course_no_lms_opens(world):
    for sql in ("select count(*) from courses where id = :course",
                "select count(*) from chunks where course_id = :course",
                "select count(*) from course_members where course_id = :course"):
        assert _count(world["stranger"], sql, world) == 0
    db = RequestSessionLocal()
    db.info["rls_user_id"] = world["stranger"]
    try:
        with pytest.raises(ProgrammingError):
            db.execute(text("insert into course_members (course_id, user_id) values (:unlinked, :stranger)"), world)
    finally:
        db.rollback()
        db.close()


def test_who_sees_a_membership(world):
    sql = "select count(*) from course_members where course_id = :course"
    assert _count(world["member"], sql, world) == 1
    assert _count(world["owner"], sql, world) == 1
    assert _count(world["stranger"], sql, world) == 0
    assert _count(None, sql, world) == 0


def test_a_teacher_member_authors_the_course_and_a_learner_does_not(world):
    """Another instructor of the course (alembic 0049) sees and writes what the owner does under it: modules,
    their parts and jobs. Not the course row itself, and not the other people's chats."""
    with SessionLocal() as db:
        teacher = User(email=f"member-teacher-{uuid.uuid4().hex[:8]}@example.com", name="TA", hashed_password="x")
        db.add(teacher)
        db.flush()
        db.add(CourseMember(course_id=world["course"], user_id=teacher.id, role="teacher"))
        db.commit()
        teacher_id = teacher.id
    try:
        for what, sql in {"modules": "select count(*) from modules where course_id = :course",
                          "module_components": "select count(*) from module_components where module_id = :module",
                          "jobs": "select count(*) from jobs where document_id = :document"}.items():
            assert _count(teacher_id, sql, world) == 1, what
        assert _count(teacher_id, "select count(*) from chat_sessions where course_id = :course", world) == 0

        db = RequestSessionLocal()
        db.info["rls_user_id"] = teacher_id
        try:
            db.add(Document(course_id=world["course"], filename="ta.pdf", status=DocumentStatus.uploaded))
            db.commit()
            assert db.execute(text("update courses set name = 'Taken' where id = :course"), world).rowcount == 0
            db.rollback()
        finally:
            db.close()
        # The learner member may still not write a document.
        db = RequestSessionLocal()
        db.info["rls_user_id"] = world["member"]
        try:
            with pytest.raises(ProgrammingError):
                db.add(Document(course_id=world["course"], filename="x.pdf", status=DocumentStatus.uploaded))
                db.commit()
        finally:
            db.rollback()
            db.close()
    finally:
        with SessionLocal() as db:
            db.query(Document).filter(Document.course_id == world["course"], Document.filename == "ta.pdf").delete()
            db.query(User).filter(User.id == teacher_id).delete()
            db.commit()
