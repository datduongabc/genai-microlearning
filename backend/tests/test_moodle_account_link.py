"""The lecturer's own Moodle account, linked by entering its username and password: the app asks
Moodle whether they are right and links only then, the Moodle password is never stored or logged,
a wrong attempt is refused and counted, a stored link to a user Moodle no longer has is cleared, and
a course cannot be linked before the account is. Moodle itself is replaced by a stand-in."""

import logging
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.db import SessionLocal, engine
from app.main import app
from app.models import User, UserRole
from app.moodle import client as moodle_client
from tests.auth_helpers import create_and_login, make_account

client = TestClient(app)

MOODLE_PASSWORD = "Moodle-Sentinel-pw-91"


@pytest.fixture
def moodle(monkeypatch):
    """The Moodle accounts whose (username, password) are right, mapped to their ids, and the calls made."""
    # The id is unique per run: the test database outlives it, and two lecturers cannot link one user.
    user_id = 10_000 + uuid.uuid4().int % 10**9
    state = {"logins": {("lan.nguyen", MOODLE_PASSWORD): user_id}, "checked": [], "user_id": user_id}

    def verify_login(username, password):
        state["checked"].append((username, password))
        return state["logins"].get((username, password))

    def forbidden(*args, **kwargs):
        raise AssertionError("the app must not create Moodle accounts")

    monkeypatch.setattr(moodle_client, "verify_login", verify_login)
    monkeypatch.setattr(moodle_client, "call", forbidden)
    return state


def _lecturer(domain="example.edu"):
    email = f"link-{uuid.uuid4().hex[:10]}@{domain}"
    make_account(email)
    login = client.post("/auth/login", json={"email": email, "password": "Tt7!kQm2#vXz"}).json()
    return email, {"Authorization": f"Bearer {login['access_token']}"}


def _link(headers, username="lan.nguyen", password=MOODLE_PASSWORD):
    return client.post("/moodle/account/link", json={"username": username, "password": password}, headers=headers)


def _stored_link(email):
    with SessionLocal() as db:
        return db.query(User).filter(User.email == email).one().moodle_user_id


def test_the_right_username_and_password_link_the_account(moodle):
    email, headers = _lecturer()
    assert client.get("/moodle/account", headers=headers).json() == {"email": email, "status": "not_linked"}

    response = _link(headers)

    assert response.status_code == 200
    assert response.json() == {"email": email, "status": "linked"}
    assert moodle["checked"] == [("lan.nguyen", MOODLE_PASSWORD)]
    assert _stored_link(email) == moodle["user_id"]
    assert client.get("/moodle/account", headers=headers).json() == {"email": email, "status": "linked"}


def test_the_username_is_trimmed_before_it_is_checked(moodle):
    _, headers = _lecturer()

    assert _link(headers, username="  lan.nguyen ").status_code == 200
    assert moodle["checked"][0][0] == "lan.nguyen"


def test_a_wrong_password_links_nothing_and_says_so(moodle):
    email, headers = _lecturer()

    response = _link(headers, password="not-the-password")

    assert response.status_code == 422
    assert response.json()["detail"] == "Tên đăng nhập hoặc mật khẩu Moodle không đúng."
    assert _stored_link(email) is None
    assert client.get("/moodle/account", headers=headers).json()["status"] == "not_linked"


def test_an_unknown_moodle_user_is_refused_the_same_way_as_a_wrong_password(moodle):
    email, headers = _lecturer()

    wrong = _link(headers, password="not-the-password")
    unknown = _link(headers, username="nobody.here")

    assert unknown.status_code == wrong.status_code == 422
    assert unknown.json() == wrong.json()
    assert _stored_link(email) is None


@pytest.mark.parametrize("body", [{}, {"username": "lan.nguyen"}, {"password": "x"}, {"username": "", "password": "x"}, {"username": "lan", "password": ""}])
def test_both_fields_are_needed(moodle, body):
    _, headers = _lecturer()

    assert client.post("/moodle/account/link", json=body, headers=headers).status_code == 422
    assert moodle["checked"] == []


def test_a_moodle_email_alone_links_nothing(moodle):
    # Linking is by username and password, not by an account that happens to have the lecturer's email.
    email, headers = _lecturer()

    assert client.post("/moodle/account/link", headers=headers).status_code == 422
    assert _stored_link(email) is None


def test_the_moodle_password_is_never_stored_or_logged(moodle, caplog):
    email, headers = _lecturer()
    caplog.set_level(logging.DEBUG)

    _link(headers)
    _link(headers, password=MOODLE_PASSWORD + "-wrong")

    assert MOODLE_PASSWORD not in caplog.text
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        assert MOODLE_PASSWORD not in repr({c.key: getattr(user, c.key) for c in inspect(User).column_attrs})
    assert not [c for c in inspect(engine).get_columns("users") if "moodle" in c["name"] and "password" in c["name"]]


def test_linking_and_a_refused_attempt_are_logged_without_the_password(moodle, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    email, headers = _lecturer()

    _link(headers, password="not-the-password")
    _link(headers)

    lines = [r.getMessage() for r in caplog.records if r.name == "app.audit"]
    assert any("moodle_account_link_refused" in line and email in line for line in lines)
    assert any("moodle_account_linked" in line and email in line for line in lines)
    assert not any("not-the-password" in line or MOODLE_PASSWORD in line for line in lines)


def test_a_moodle_account_another_lecturer_linked_cannot_be_linked_again(moodle):
    first_email, first = _lecturer()
    second_email, second = _lecturer()
    assert _link(first).status_code == 200

    refused = _link(second)

    assert refused.status_code == 409
    assert _stored_link(second_email) is None and _stored_link(first_email) == moodle["user_id"]
    assert _link(first).status_code == 200  # the owner may confirm it again


def test_linking_again_with_another_moodle_account_replaces_the_link(moodle):
    email, headers = _lecturer()
    moodle["logins"][("lan.second", "Second-pass-1")] = moodle["user_id"] + 1
    _link(headers)

    assert _link(headers, username="lan.second", password="Second-pass-1").status_code == 200
    assert _stored_link(email) == moodle["user_id"] + 1


@pytest.mark.rate_limits
def test_attempts_are_rate_limited_per_lecturer(moodle):
    _, headers = _lecturer()
    _, other = _lecturer()

    codes = [_link(headers, password=f"guess-{i}").status_code for i in range(6)]

    assert codes[:5] == [422] * 5 and codes[5] == 429
    assert _link(other).status_code == 200  # another lecturer has their own budget


def test_a_stored_link_to_a_moodle_user_that_is_gone_is_cleared_and_can_be_made_again(moodle, monkeypatch):
    email, headers = _lecturer()
    _link(headers)
    monkeypatch.setattr(moodle_client, "user_exists", lambda user_id: False)  # a Moodle admin deleted it

    assert client.get("/moodle/account", headers=headers).json() == {"email": email, "status": "not_linked"}
    assert _stored_link(email) is None
    course_id = client.post("/courses", json={"name": "Lập trình"}, headers=headers).json()["id"]
    assert client.post(f"/courses/{course_id}/create-moodle", headers=headers).status_code == 422

    monkeypatch.setattr(moodle_client, "user_exists", lambda user_id: True)
    moodle["logins"][("lan.nguyen", MOODLE_PASSWORD)] = moodle["user_id"] + 2
    assert _link(headers).status_code == 200
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"
    assert _stored_link(email) == moodle["user_id"] + 2


def test_a_stored_link_is_kept_while_moodle_cannot_be_asked(moodle, monkeypatch):
    _, headers = _lecturer()
    _link(headers)

    def down(user_id):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(moodle_client, "user_exists", down)
    assert client.get("/moodle/account", headers=headers).status_code == 502

    monkeypatch.setattr(moodle_client, "user_exists", lambda user_id: True)
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"


def test_when_moodle_cannot_be_reached_the_answer_says_so(monkeypatch):
    email, headers = _lecturer()

    def down(username, password):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(moodle_client, "verify_login", down)

    response = _link(headers)
    assert response.status_code == 502
    assert _stored_link(email) is None


def test_when_moodle_refuses_the_connection_the_lecturer_is_told_to_contact_the_admin(monkeypatch):
    _, headers = _lecturer()

    def refused(username, password):
        raise moodle_client.MoodleError("local_lms_bridge_verify_login: Access control exception", "accessexception")

    monkeypatch.setattr(moodle_client, "verify_login", refused)

    response = _link(headers)
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "quản trị viên" in detail
    assert "verify_login" not in detail and "Access control" not in detail


def test_the_app_creates_no_moodle_accounts():
    assert not hasattr(moodle_client, "create_user")
    from app.moodle import accounts

    assert not hasattr(accounts, "ensure_moodle_user")
    assert "/moodle/account/prompt/dismiss" not in app.openapi()["paths"]


def test_the_prompt_flag_is_gone_from_the_users_table():
    assert "moodle_prompted_at" not in {c["name"] for c in inspect(engine).get_columns("users")}
    assert not hasattr(User, "moodle_prompted_at")


def test_an_admin_has_no_moodle_account_to_link_and_a_stranger_is_turned_away(moodle):
    login = create_and_login(client, f"adm-{uuid.uuid4().hex[:8]}@example.edu", role=UserRole.admin)
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    assert client.get("/moodle/account", headers=headers).status_code == 404
    assert _link(headers).status_code == 404
    assert moodle["checked"] == []
    assert client.get("/moodle/account").status_code == 401
    assert client.post("/moodle/account/link", json={"username": "a", "password": "b"}).status_code == 401


def test_a_course_cannot_be_linked_or_created_in_moodle_before_the_account_is(moodle):
    _, headers = _lecturer()
    course_id = client.post("/courses", json={"name": "Lập trình"}, headers=headers).json()["id"]
    moodle_id = 1_000 + uuid.uuid4().int % 10**9  # unique: the claims table outlives the run

    linking = client.post(f"/courses/{course_id}/link-moodle", json={"moodle_course_id": moodle_id}, headers=headers)
    creating = client.post(f"/courses/{course_id}/create-moodle", headers=headers)

    for refused in (linking, creating):
        assert refused.status_code == 422
        assert "tên đăng nhập và mật khẩu Moodle" in refused.json()["detail"]

    _link(headers)
    linked = client.post(f"/courses/{course_id}/link-moodle", json={"moodle_course_id": moodle_id}, headers=headers)
    assert linked.status_code == 200
