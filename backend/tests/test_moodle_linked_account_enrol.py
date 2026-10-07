"""A lecturer is tied to the Moodle account they linked with its username and password: creating a
Moodle course and publishing enrol that account as the course's teacher, and a lecturer who has not
linked one, even when Moodle has a user with their email, gets a clear error and nothing is pushed.
Moodle itself is replaced by a recorder."""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth.security import decode_token, token_user_id
from app.db import SessionLocal
from app.main import app
from app.models import ComponentType, Course, Document, Module, ModuleComponent, User
from app.moodle import client as moodle_client
from tests.auth_helpers import create_and_login, link_moodle_account, make_account

client = TestClient(app)


@pytest.fixture
def moodle(monkeypatch):
    """A record of what was written to Moodle."""
    # A Moodle course id is unique in the table and the test database outlives the run.
    created_id = 1_000 + uuid.uuid4().int % 10**9
    # Likewise the id of the Moodle user: two lecturers cannot link one.
    state = {"calls": [], "course_id": created_id, "user_id": 10_000 + uuid.uuid4().int % 10**9}

    monkeypatch.setattr(
        moodle_client, "create_course", lambda name: state["calls"].append(("create_course", name)) or created_id
    )
    monkeypatch.setattr(
        moodle_client, "enrol_as_teacher", lambda course_id, user_id: state["calls"].append(("enrol", course_id, user_id))
    )
    monkeypatch.setattr(
        moodle_client, "create_book", lambda *args, **kwargs: state["calls"].append(("book",)) or 101
    )
    monkeypatch.setattr(
        moodle_client, "create_quiz", lambda *args, **kwargs: state["calls"].append(("quiz",)) or 202
    )
    return state


def _lecturer(email=None):
    email = email or f"teach-{uuid.uuid4().hex[:10]}@example.com"
    login = create_and_login(client, email)
    return email, {"Authorization": f"Bearer {login['access_token']}"}


def _course(headers):
    return client.post("/courses", json={"name": "Lập trình"}, headers=headers).json()["id"]


def _link_account(moodle, headers):
    """The lecturer has the Moodle link their first Moodle login stores (test_moodle_login.py tests that)."""
    user_id = token_user_id(decode_token(headers["Authorization"].split()[1]))
    link_moodle_account(user_id, moodle["user_id"])


def test_the_course_teacher_is_the_moodle_account_the_lecturer_linked(moodle):
    _, headers = _lecturer()
    _link_account(moodle, headers)
    course_id = _course(headers)

    response = client.post(f"/courses/{course_id}/create-moodle", headers=headers)

    assert response.status_code == 200
    assert moodle["calls"] == [("create_course", "Lập trình"), ("enrol", moodle["course_id"], moodle["user_id"])]


def test_a_lecturer_who_has_not_linked_a_moodle_account_is_refused_and_nothing_is_pushed(moodle):
    _, headers = _lecturer()
    course_id = _course(headers)

    response = client.post(f"/courses/{course_id}/create-moodle", headers=headers)

    assert response.status_code == 422
    assert "đăng nhập lại bằng tài khoản Moodle" in response.json()["detail"]
    assert moodle["calls"] == []  # no Moodle course made, so none is left behind
    courses = {c["id"]: c for c in client.get("/courses", headers=headers).json()}
    assert courses[course_id]["moodle_course_id"] is None


def test_the_token_owner_is_not_enrolled_any_more(moodle):
    _, headers = _lecturer()
    _link_account(moodle, headers)

    client.post(f"/courses/{_course(headers)}/create-moodle", headers=headers)

    assert not any(call[0] == "enrol" and call[2] != moodle["user_id"] for call in moodle["calls"])


def _publishable_module(headers):
    course_id = _course(headers)
    with SessionLocal() as db:
        course = db.get(Course, uuid.UUID(course_id))
        course.moodle_course_id = 1_000 + uuid.uuid4().int % 10**9
        document = Document(course_id=course.id, filename="x.pdf")
        db.add(document)
        db.flush()
        module = Module(document_id=document.id, course_id=course.id, title="Bài 1", learning_objective="Hiểu A")
        db.add(module)
        db.flush()
        db.add(ModuleComponent(module_id=module.id, type=ComponentType.content, content={"markdown": "Nội dung"}))
        db.commit()
        return module.id


def test_publishing_needs_the_linked_account_and_enrols_it_before_writing_anything(moodle):
    _, headers = _lecturer()
    module_id = _publishable_module(headers)

    refused = client.post(f"/modules/{module_id}/publish-to-moodle", json={"section_num": 0}, headers=headers)

    assert refused.status_code == 422 and "đăng nhập lại bằng tài khoản Moodle" in refused.json()["detail"]
    assert moodle["calls"] == []

    _link_account(moodle, headers)
    accepted = client.post(f"/modules/{module_id}/publish-to-moodle", json={"section_num": 0}, headers=headers)

    assert accepted.status_code == 200
    moodle_course_id = accepted.json()["moodle_course_id"]
    assert moodle["calls"] == [("enrol", moodle_course_id, moodle["user_id"]), ("book",)]


def test_publishing_again_enrols_again_which_moodle_takes_as_no_change(moodle):
    _, headers = _lecturer()
    _link_account(moodle, headers)
    module_id = _publishable_module(headers)

    for _ in range(2):
        response = client.post(f"/modules/{module_id}/publish-to-moodle", json={"section_num": 0}, headers=headers)
        assert response.status_code == 200

    assert [call[0] for call in moodle["calls"]] == ["enrol", "book", "enrol", "book"]


def test_when_enrolling_fails_nothing_is_published_and_the_error_says_so(moodle, monkeypatch):
    _, headers = _lecturer()
    _link_account(moodle, headers)
    module_id = _publishable_module(headers)

    def down(course_id, user_id):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(moodle_client, "enrol_as_teacher", down)

    response = client.post(f"/modules/{module_id}/publish-to-moodle", json={"section_num": 0}, headers=headers)

    assert response.status_code == 502
    assert "Moodle" in response.json()["detail"]
    assert moodle["calls"] == []


def _gone(monkeypatch):
    """The lecturer's linked Moodle user is deleted in Moodle: enrolling them fails, and a lookup
    no longer finds them."""
    monkeypatch.setattr(moodle_client, "user_exists", lambda user_id: False)

    def refuse(course_id, user_id):
        raise moodle_client.MoodleError("enrol_manual_enrol_users: User ID does not exist or is deleted!")

    monkeypatch.setattr(moodle_client, "enrol_as_teacher", refuse)


def _stored_link(email):
    with SessionLocal() as db:
        return db.query(User).filter(User.email == email.lower()).one().moodle_user_id


def test_publishing_with_a_linked_moodle_user_that_is_gone_asks_to_link_again(moodle, monkeypatch):
    email, headers = _lecturer()
    _link_account(moodle, headers)
    module_id = _publishable_module(headers)
    _gone(monkeypatch)

    response = client.post(f"/modules/{module_id}/publish-to-moodle", json={"section_num": 0}, headers=headers)

    assert response.status_code == 422 and "liên kết lại" in response.json()["detail"]
    assert moodle["calls"] == []
    assert _stored_link(email) is None


@pytest.mark.parametrize("action", ["link", "create"])
def test_linking_or_creating_a_course_with_a_linked_moodle_user_that_is_gone_asks_to_link_again(
    moodle, monkeypatch, action
):
    email, headers = _lecturer()
    _link_account(moodle, headers)
    course_id = _course(headers)
    _gone(monkeypatch)

    if action == "link":
        response = client.post(
            f"/courses/{course_id}/link-moodle", json={"moodle_course_id": moodle["course_id"]}, headers=headers
        )
    else:
        response = client.post(f"/courses/{course_id}/create-moodle", headers=headers)

    assert response.status_code == 422 and "liên kết lại" in response.json()["detail"]
    assert _stored_link(email) is None


def test_a_moodle_timeout_while_enrolling_on_link_is_a_clear_error_and_the_link_stays(moodle, monkeypatch):
    email, headers = _lecturer()
    _link_account(moodle, headers)
    course_id = _course(headers)

    def timeout(course_id, user_id):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(moodle_client, "enrol_as_teacher", timeout)

    response = client.post(
        f"/courses/{course_id}/link-moodle", json={"moodle_course_id": moodle["course_id"]}, headers=headers
    )

    assert response.status_code == 502 and "was linked" in response.json()["detail"]
    courses = {c["id"]: c["moodle_course_id"] for c in client.get("/courses", headers=headers).json()}
    assert courses[course_id] == moodle["course_id"]
    assert _stored_link(email) == moodle["user_id"]


def test_make_account_helper_creates_a_lecturer_not_an_admin():
    user = make_account(f"helper-{uuid.uuid4().hex[:8]}@example.com")

    assert user.role.value == "teacher" and user.is_active is True
