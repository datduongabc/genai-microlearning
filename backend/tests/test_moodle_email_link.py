"""A lecturer is tied to their Moodle account by email: pushing a course enrols the Moodle user
whose email matches theirs (case-insensitively) as its teacher, and a lecturer with no such
account gets a clear error and nothing is pushed. Moodle itself is replaced by a recorder."""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import ComponentType, Course, Document, Module, ModuleComponent
from app.moodle import client as moodle_client
from tests.auth_helpers import create_and_login, make_account

client = TestClient(app)


@pytest.fixture
def moodle(monkeypatch):
    """Moodle's users by email (keys as typed there) and a record of what was written."""
    # A Moodle course id is unique in the table and the test database outlives the run.
    created_id = 1_000 + uuid.uuid4().int % 10**9
    state = {"users": {}, "calls": [], "course_id": created_id}

    def find_users_by_email(emails):
        by_lower = {}
        for email, ids in state["users"].items():
            by_lower.setdefault(email.lower(), []).extend(ids)
        return {e.strip().lower(): by_lower.get(e.strip().lower(), []) for e in emails}

    monkeypatch.setattr(moodle_client, "find_users_by_email", find_users_by_email)
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


def test_the_course_teacher_is_the_moodle_account_with_the_lecturers_email(moodle):
    email, headers = _lecturer()
    moodle["users"][email] = [31]
    moodle["users"]["someone-else@example.com"] = [99]
    course_id = _course(headers)

    response = client.post(f"/courses/{course_id}/create-moodle", headers=headers)

    assert response.status_code == 200
    assert moodle["calls"] == [("create_course", "Lập trình"), ("enrol", moodle["course_id"], 31)]


def test_the_email_match_ignores_case_on_either_side(moodle):
    email, headers = _lecturer(f"Mixed.Case-{uuid.uuid4().hex[:8]}@Example.com")
    moodle["users"][email.upper()] = [44]

    response = client.post(f"/courses/{_course(headers)}/create-moodle", headers=headers)

    assert response.status_code == 200
    assert ("enrol", moodle["course_id"], 44) in moodle["calls"]


def test_a_lecturer_without_a_moodle_account_gets_a_clear_error_and_nothing_is_pushed(moodle):
    email, headers = _lecturer()
    moodle["users"]["someone-else@example.com"] = [99]
    course_id = _course(headers)

    response = client.post(f"/courses/{course_id}/create-moodle", headers=headers)

    assert response.status_code == 422
    assert email in response.json()["detail"] and "Moodle" in response.json()["detail"]
    assert moodle["calls"] == []  # no Moodle course made, so none is left behind
    courses = {c["id"]: c for c in client.get("/courses", headers=headers).json()}
    assert courses[course_id]["moodle_course_id"] is None


def test_two_moodle_accounts_with_one_email_do_not_pick_a_teacher_by_guessing(moodle):
    email, headers = _lecturer()
    moodle["users"][email] = [31, 32]

    response = client.post(f"/courses/{_course(headers)}/create-moodle", headers=headers)

    assert response.status_code == 422
    assert moodle["calls"] == []


def test_when_moodle_cannot_be_reached_nothing_is_pushed_and_the_error_says_so(moodle, monkeypatch):
    _, headers = _lecturer()

    def down(emails):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(moodle_client, "find_users_by_email", down)

    response = client.post(f"/courses/{_course(headers)}/create-moodle", headers=headers)

    assert response.status_code == 502
    assert moodle["calls"] == []


def test_the_token_owner_is_not_enrolled_any_more(moodle):
    email, headers = _lecturer()
    moodle["users"][email] = [31]

    client.post(f"/courses/{_course(headers)}/create-moodle", headers=headers)

    assert not any(call[0] == "enrol" and call[2] != 31 for call in moodle["calls"])


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


def test_publishing_a_module_needs_the_lecturers_moodle_account_too(moodle):
    email, headers = _lecturer()
    module_id = _publishable_module(headers)

    refused = client.post(f"/modules/{module_id}/publish-to-moodle", json={"section_num": 0}, headers=headers)

    assert refused.status_code == 422 and email in refused.json()["detail"]
    assert moodle["calls"] == []

    moodle["users"][email] = [31]
    accepted = client.post(f"/modules/{module_id}/publish-to-moodle", json={"section_num": 0}, headers=headers)

    assert accepted.status_code == 200
    assert ("book",) in moodle["calls"]


def test_make_account_helper_creates_a_lecturer_not_an_admin():
    user = make_account(f"helper-{uuid.uuid4().hex[:8]}@example.com")

    assert user.role.value == "teacher" and user.is_active is True
