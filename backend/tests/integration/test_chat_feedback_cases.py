"""Answers readers flagged, or asked again at once, become regression cases, once each and without names."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db import SessionLocal
from app.models import ChatMessage, ChatRole, ChatSession, Course, User
from scripts import chat_feedback_cases


def test_asked_again_means_nearly_the_same_words():
    assert chat_feedback_cases.asked_again("Heap là gì?", "heap là cái gì vậy")
    assert not chat_feedback_cases.asked_again("Heap là gì?", "Còn stack thì sao?")


@pytest.fixture
def chats():
    now = datetime.now(UTC)
    with SessionLocal() as db:
        user = User(email=f"cases-{uuid.uuid4().hex}@example.com", name="Hidden Name", hashed_password="x")
        db.add(user)
        db.flush()
        course = Course(name="Cases", owner_id=user.id)
        db.add(course)
        db.flush()
        session = ChatSession(course_id=course.id, user_id=user.id)
        db.add(session)
        db.flush()

        def say(role, content, seconds, **extra):
            db.add(ChatMessage(session_id=session.id, role=role, content=content, created_at=now + timedelta(seconds=seconds),
                               citations=extra.pop("citations", []), **extra))

        cited = [{"chunk_id": "c1", "source_ref": "p1"}, {"chunk_id": "c2", "source_ref": "p2"}]
        say(ChatRole.user, "Heap là gì?", 0)
        say(ChatRole.assistant, "Một stack [S1][S2].", 1, citations=cited, feedback="wrong_citation",
            feedback_citations=[2], feedback_note="nguồn 2 sai", trace={"rewritten": None, "route": "answered"})
        say(ChatRole.user, "Deadlock là gì?", 10)
        say(ChatRole.assistant, "Không rõ.", 11)
        say(ChatRole.user, "deadlock là cái gì", 40)
        say(ChatRole.assistant, "Bế tắc.", 41, feedback="helpful")
        db.commit()
        ids = {"user": user.id, "course": course.id}
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()


def test_flagged_and_asked_again_answers_are_exported_once(chats, tmp_path):
    out = tmp_path / "cases.jsonl"
    chat_feedback_cases.export(out)
    chat_feedback_cases.export(out)
    cases = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    mine = [c for c in cases if c["course_id"] == str(chats["course"])]
    assert [c["kind"] for c in mine] == ["wrong_citation", "asked_again"]
    assert mine[0]["flagged"] == ["c2"] and mine[0]["note"] == "nguồn 2 sai"
    assert mine[1]["asked_next"] == "deadlock là cái gì"
    assert "Hidden Name" not in out.read_text(encoding="utf-8")
    assert str(chats["user"]) not in out.read_text(encoding="utf-8")
