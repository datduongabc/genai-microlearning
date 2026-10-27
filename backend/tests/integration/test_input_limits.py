"""Nothing a client sends is unbounded: every text field has a maximum and a JSON body
has a maximum size. Going over is a clean 4xx answered before any expensive work (a
model call, a file write, a request to Moodle) starts."""

import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.chat import MAX_CHAT_MESSAGE_CHARS, ChatRequest
from app.auth.security import create_access_token
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Course, Document, DocumentStatus, Module, User

client = TestClient(app)
ONE_MEGABYTE = 1024 * 1024


@pytest.fixture(autouse=True)
def isolated_upload_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))


@pytest.fixture
def world():
    db = SessionLocal()
    try:
        user = User(
            email=f"limits-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False, moodle_user_id=10_000 + uuid.uuid4().int % 10**9
        )
        db.add(user)
        db.flush()
        course = Course(name="Limits", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="a.txt", status=DocumentStatus.embedded, raw_text="text")
        db.add(doc)
        db.flush()
        module = Module(document_id=doc.id, course_id=course.id, title="m")
        db.add(module)
        db.commit()
        ids = {"user": user.id, "course": course.id, "document": doc.id, "module": module.id}
    finally:
        db.close()

    yield {**ids, "headers": {"Authorization": f"Bearer {create_access_token(ids['user'])}"}}

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.owner_id == ids["user"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_a_course_name_has_a_maximum_and_cannot_be_blank(world):
    headers = world["headers"]

    assert client.post("/courses", json={"name": "N" * 200}, headers=headers).status_code == 200
    assert client.post("/courses", json={"name": "N" * 201}, headers=headers).status_code == 422
    assert client.post("/courses", json={"name": ""}, headers=headers).status_code == 422
    assert client.post("/courses", json={"name": "   "}, headers=headers).status_code == 422


def test_a_course_name_is_stored_trimmed(world):
    created = client.post("/courses", json={"name": "  Data   structures  "}, headers=world["headers"]).json()

    assert created["name"] == "Data structures"


def test_a_chat_message_has_a_maximum_length_that_is_checked_before_anything_else(world):
    too_long = "x" * (MAX_CHAT_MESSAGE_CHARS + 1)

    response = client.post(f"/courses/{world['course']}/chat", json={"message": too_long}, headers=world["headers"])

    assert response.status_code == 422
    ChatRequest(message="x" * MAX_CHAT_MESSAGE_CHARS)
    with pytest.raises(ValidationError):
        ChatRequest(message=too_long)


@pytest.mark.parametrize("moodle_id", [0, -1, 2**31, 10**12])
def test_a_moodle_course_id_outside_the_range_of_ids_is_refused(world, moodle_id):
    response = client.post(
        f"/courses/{world['course']}/link-moodle", json={"moodle_course_id": moodle_id}, headers=world["headers"]
    )

    assert response.status_code == 422


@pytest.mark.parametrize("section", [-1, 1001, 10**9])
def test_a_moodle_section_number_outside_a_sane_range_is_refused(world, section):
    response = client.post(
        f"/modules/{world['module']}/publish-to-moodle", json={"section_num": section}, headers=world["headers"]
    )

    assert response.status_code == 422


def test_a_module_title_has_a_maximum(world):
    ok = client.patch(f"/modules/{world['module']}", params={"title": "T" * 200}, headers=world["headers"])
    too_long = client.patch(f"/modules/{world['module']}", params={"title": "T" * 201}, headers=world["headers"])

    assert (ok.status_code, too_long.status_code) == (200, 422)


def test_a_json_body_over_one_megabyte_is_refused_with_413(world):
    response = client.post("/courses", json={"name": "N" * (ONE_MEGABYTE + 1)}, headers=world["headers"])

    assert response.status_code == 413


def test_a_body_that_only_shows_its_size_while_streaming_is_refused_too(world):
    def chunks():
        yield b'{"name": "'
        for _ in range(3):
            yield b"N" * (ONE_MEGABYTE // 2)
        yield b'"}'

    response = client.post(
        "/courses", content=chunks(), headers={**world["headers"], "Content-Type": "application/json"}
    )

    assert response.status_code == 413


def test_a_multipart_body_is_limited_like_any_other_except_for_the_upload_itself(world):
    big = b"a" * (2 * ONE_MEGABYTE)

    elsewhere = client.post("/courses", files={"file": ("x.txt", big, "text/plain")}, headers=world["headers"])
    upload = client.post(
        "/documents", params={"course_id": world["course"]}, files={"file": ("big.html", big, "text/html")},
        headers=world["headers"],
    )

    assert elsewhere.status_code == 413
    assert upload.status_code == 200


def test_a_body_under_the_limit_is_untouched(world):
    response = client.post("/courses", json={"name": "N" * 200}, headers=world["headers"])

    assert response.status_code == 200


def test_a_refresh_token_field_has_a_maximum_length():
    assert client.post("/auth/refresh", json={"refresh_token": "t" * 201}).status_code == 422
    assert client.post("/auth/logout", json={"refresh_token": "t" * 201}).status_code == 422
