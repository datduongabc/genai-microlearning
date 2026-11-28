"""An author opens or closes each document to the course's learners; a new document starts closed."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Course, CourseMember, Document, DocumentStatus, User

client = TestClient(app)


@pytest.fixture
def world():
    with SessionLocal() as db:
        people = {name: User(email=f"access-{name}-{uuid.uuid4().hex[:8]}@example.com", name=name,
                             hashed_password="x") for name in ("owner", "learner", "stranger")}
        db.add_all(people.values())
        db.flush()
        course = Course(name="Access", owner_id=people["owner"].id)
        db.add(course)
        db.flush()
        db.add(CourseMember(course_id=course.id, user_id=people["learner"].id))
        doc = Document(course_id=course.id, filename="key.pdf", status=DocumentStatus.embedded)
        db.add(doc)
        db.commit()
        ids = {**{k: u.id for k, u in people.items()}, "course": course.id, "document": doc.id}
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids["owner"], ids["learner"], ids["stranger"]])).delete(
            synchronize_session=False)
        db.commit()


def _as(user_id):
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


def _listed(world):
    response = client.get(f"/documents?course_id={world['course']}", headers=_as(world["owner"]))
    return response.json()[0]["learner_access"]


def test_a_new_document_is_closed_and_its_author_opens_and_closes_it(world):
    assert _listed(world) is False
    url = f"/documents/{world['document']}"
    assert client.patch(url, json={"learner_access": True}, headers=_as(world["owner"])).json()["learner_access"]
    assert _listed(world) is True
    client.patch(url, json={"learner_access": False}, headers=_as(world["owner"]))
    assert _listed(world) is False


@pytest.mark.parametrize("who", ["learner", "stranger"])
def test_only_an_author_changes_who_may_ask_about_a_document(world, who):
    response = client.patch(f"/documents/{world['document']}", json={"learner_access": True}, headers=_as(world[who]))
    assert response.status_code == 404
    assert _listed(world) is False
