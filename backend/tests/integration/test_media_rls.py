"""What the database enforces for videos and flashcard sets (migration 0032), connected as the role the
requests use: the course owner has all of them, a member of the course reads only what is published, and
nobody else sees anything."""

import uuid

import pytest
from sqlalchemy import text

from app.db import RequestSessionLocal, SessionLocal
from app.models import Course, CourseMember, Document, DocumentStatus, User
from app.models.media import FlashcardSet, MediaStatus, Video


@pytest.fixture()
def world():
    with SessionLocal() as db:
        people = {name: User(email=f"media-{name}-{uuid.uuid4().hex[:8]}@example.com", name=name, hashed_password="x")
                  for name in ("owner", "other", "member", "outsider")}
        db.add_all(people.values())
        db.flush()
        course = Course(name="Course", owner_id=people["owner"].id)
        elsewhere = Course(name="Elsewhere", owner_id=people["outsider"].id)
        db.add_all([course, elsewhere])
        db.flush()
        document = Document(course_id=course.id, filename="notes.pdf", status=DocumentStatus.embedded)
        db.add(document)
        db.flush()
        db.add(CourseMember(course_id=course.id, user_id=people["member"].id))
        db.add(CourseMember(course_id=elsewhere.id, user_id=people["other"].id))
        db.commit()
        ids = {**{k: u.id for k, u in people.items()}, "course": course.id, "elsewhere": elsewhere.id,
               "document": document.id}
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id.in_([ids["course"], ids["elsewhere"]])).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids[k] for k in ("owner", "other", "member", "outsider")])).delete(
            synchronize_session=False)
        db.commit()


def _as(user_id):
    db = RequestSessionLocal()
    db.info["rls_user_id"] = user_id
    return db


def _video(ids, **kw):
    return Video(course_id=ids["course"], document_id=ids["document"], output_language="vi",
                 model_alias="gemini-fast", voice_alias="voice-heygen", image_alias="image-nano-banana", **kw)


def _set(ids, **kw):
    return FlashcardSet(course_id=ids["course"], document_id=ids["document"], output_language="vi",
                        model_alias="gemini-fast", **kw)


def test_owner_reads_and_writes_their_course_videos_and_sets(world):
    db = _as(world["owner"])
    try:
        video, cards = _video(world), _set(world, cards=[{"front": "f", "back": "b", "citations": [],
                                                           "author": "model", "flag": None}])
        db.add_all([video, cards])
        db.commit()
        assert video.status == MediaStatus.generating and video.draft_version == 1 and video.cost == {}
        assert db.execute(text("select count(*) from videos")).scalar_one() == 1
        assert db.execute(text("select count(*) from flashcard_sets")).scalar_one() == 1
        video.status = MediaStatus.draft
        cards.focus = "chapter 2"
        db.commit()
        db.delete(video)
        db.commit()
        assert db.execute(text("select count(*) from videos")).scalar_one() == 0
    finally:
        db.close()


def test_another_lecturer_sees_nothing(world):
    with SessionLocal() as db:
        db.add_all([_video(world, published_at=text("now()")), _set(world, published_at=text("now()"))])
        db.commit()
    db = _as(world["outsider"])
    try:
        assert db.execute(text("select count(*) from videos")).scalar_one() == 0
        assert db.execute(text("select count(*) from flashcard_sets")).scalar_one() == 0
        assert db.execute(text("update videos set error = 'x'")).rowcount == 0
        assert db.execute(text("delete from flashcard_sets")).rowcount == 0
    finally:
        db.rollback()
        db.close()


def test_a_member_sees_only_published_items_of_their_course(world):
    with SessionLocal() as db:
        hidden, shown = _video(world), _video(world, published_at=text("now()"))
        hidden_set, shown_set = _set(world), _set(world, published_at=text("now()"))
        db.add_all([hidden, shown, hidden_set, shown_set])
        db.commit()
        shown_id, shown_set_id = shown.id, shown_set.id
    db = _as(world["member"])
    try:
        assert [r[0] for r in db.execute(text("select id from videos"))] == [shown_id]
        assert [r[0] for r in db.execute(text("select id from flashcard_sets"))] == [shown_set_id]
        assert db.execute(text("update videos set error = 'x'")).rowcount == 0
        assert db.execute(text("update flashcard_sets set focus = 'x'")).rowcount == 0
        assert db.execute(text("delete from videos")).rowcount == 0
        assert db.execute(text("delete from flashcard_sets")).rowcount == 0
    finally:
        db.rollback()
        db.close()


def test_a_member_of_another_course_sees_nothing(world):
    with SessionLocal() as db:
        db.add_all([_video(world, published_at=text("now()")), _set(world, published_at=text("now()"))])
        db.commit()
    db = _as(world["other"])
    try:
        assert db.execute(text("select count(*) from videos")).scalar_one() == 0
        assert db.execute(text("select count(*) from flashcard_sets")).scalar_one() == 0
    finally:
        db.close()


def test_deleting_the_document_deletes_its_videos_and_sets(world):
    with SessionLocal() as db:
        db.add_all([_video(world), _set(world)])
        db.commit()
        db.delete(db.get(Document, world["document"]))
        db.commit()
        assert db.execute(text("select count(*) from videos where course_id = :c"), {"c": world["course"]}).scalar_one() == 0
        assert db.execute(text("select count(*) from flashcard_sets where course_id = :c"), {"c": world["course"]}).scalar_one() == 0


def test_the_worker_writes_for_every_tenant(world):
    from app.db import WorkerSessionLocal

    with SessionLocal() as db:
        db.add(_video(world))
        db.commit()
    db = WorkerSessionLocal()
    try:
        assert db.execute(text("update videos set error = 'x' where course_id = :c"), {"c": world["course"]}).rowcount == 1
    finally:
        db.rollback()
        db.close()
