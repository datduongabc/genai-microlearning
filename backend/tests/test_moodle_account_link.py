"""The lecturer's own Moodle account, linked by their choice (the pop-up's "Yes") and remembered:
the Moodle user with their email is linked, one is created when there is none, only an edu address
is created, and a course cannot be linked before the account is, even when Moodle has their email."""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth.accounts import is_edu_email
from app.db import SessionLocal
from app.main import app
from app.models import User, UserRole
from app.moodle import client as moodle_client
from tests.auth_helpers import create_and_login, make_account

client = TestClient(app)


@pytest.fixture
def moodle(monkeypatch):
    """Moodle's users by email and a record of the users created."""
    state = {"users": {}, "created": [], "next_id": 7_000}

    def find_users_by_email(emails):
        by_lower = {}
        for email, ids in state["users"].items():
            by_lower.setdefault(email.lower(), []).extend(ids)
        return {e.strip().lower(): by_lower.get(e.strip().lower(), []) for e in emails}

    def create_user(email, firstname, lastname, password):
        state["next_id"] += 1
        state["created"].append({"email": email, "firstname": firstname, "lastname": lastname, "password": password})
        state["users"][email] = [state["next_id"]]
        return state["next_id"]

    monkeypatch.setattr(moodle_client, "find_users_by_email", find_users_by_email)
    monkeypatch.setattr(moodle_client, "create_user", create_user)
    return state


def _lecturer(domain="example.edu", name="Nguyễn Thị Lan"):
    email = f"link-{uuid.uuid4().hex[:10]}@{domain}"
    make_account(email, name=name)
    login = client.post("/auth/login", json={"email": email, "password": "Tt7!kQm2#vXz"}).json()
    return email, {"Authorization": f"Bearer {login['access_token']}"}


@pytest.mark.parametrize("email", ["a@hcmut.edu.vn", "a@x.edu", "a@Mail.HCMUT.EDU.VN", "a@x.ac.edu"])
def test_edu_addresses_are_accepted(email):
    assert is_edu_email(email)


@pytest.mark.parametrize(
    "email", ["a@gmail.com", "a@example.com", "a@edu.com", "a@edu", "a@x.edu.com", "a@school.edu.vn.evil.com", "a", ""]
)
def test_other_addresses_are_refused(email):
    assert not is_edu_email(email)


def test_the_account_is_not_linked_until_the_lecturer_says_yes_even_when_moodle_has_their_email(moodle):
    email, headers = _lecturer()

    not_linked = {"email": email, "status": "not_linked", "prompt": True}
    assert client.get("/moodle/account", headers=headers).json() == not_linked

    moodle["users"][email.upper()] = [55]
    assert client.get("/moodle/account", headers=headers).json() == not_linked

    client.post("/moodle/account/link", headers=headers)
    assert client.get("/moodle/account", headers=headers).json() == {"email": email, "status": "linked", "prompt": False}


def test_a_stored_link_to_a_moodle_user_that_is_gone_is_cleared_and_yes_links_again(moodle, monkeypatch):
    email, headers = _lecturer()
    client.post("/moodle/account/link", headers=headers)
    first_id = moodle["users"][email][0]
    del moodle["users"][email]  # a Moodle admin deleted it
    monkeypatch.setattr(
        moodle_client, "user_exists", lambda user_id: any(user_id in ids for ids in moodle["users"].values())
    )

    # Only the status flips: the pop-up was answered (Yes) and does not come back.
    assert client.get("/moodle/account", headers=headers).json() == {"email": email, "status": "not_linked", "prompt": False}
    course_id = client.post("/courses", json={"name": "Lập trình"}, headers=headers).json()["id"]
    assert client.post(f"/courses/{course_id}/create-moodle", headers=headers).status_code == 422

    relinked = client.post("/moodle/account/link", headers=headers)

    assert relinked.json()["created"] is True and moodle["users"][email] != [first_id]
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"


def test_no_answers_the_pop_up_for_good_and_the_account_can_still_be_linked_later(moodle):
    email, headers = _lecturer()

    dismissed = client.post("/moodle/account/prompt/dismiss", headers=headers)

    assert dismissed.status_code == 200
    assert client.get("/moodle/account", headers=headers).json() == {"email": email, "status": "not_linked", "prompt": False}
    # A new login does not ask again: the answer is kept with the account, not in the browser.
    client.post("/auth/login", json={"email": email, "password": "Tt7!kQm2#vXz"})
    assert client.get("/moodle/account", headers=headers).json()["prompt"] is False
    assert client.post("/moodle/account/link", headers=headers).status_code == 200
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"


def test_answering_records_the_time_once_and_a_second_answer_keeps_the_first(moodle):
    email, headers = _lecturer()

    def prompted_at():
        with SessionLocal() as db:
            return db.query(User).filter(User.email == email).one().moodle_prompted_at

    assert prompted_at() is None
    client.post("/moodle/account/link", headers=headers)
    first = prompted_at()
    client.post("/moodle/account/prompt/dismiss", headers=headers)

    assert first is not None and prompted_at() == first


def test_several_moodle_users_with_the_email_still_get_the_pop_up_once(moodle):
    email, headers = _lecturer()
    moodle["users"][email] = [1, 2]

    assert client.get("/moodle/account", headers=headers).json() == {"email": email, "status": "ambiguous", "prompt": True}
    client.post("/moodle/account/prompt/dismiss", headers=headers)
    assert client.get("/moodle/account", headers=headers).json()["prompt"] is False


def test_a_stored_link_is_kept_while_moodle_cannot_be_asked(moodle, monkeypatch):
    _, headers = _lecturer()
    client.post("/moodle/account/link", headers=headers)

    def down(user_id):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(moodle_client, "user_exists", down)
    assert client.get("/moodle/account", headers=headers).status_code == 502

    monkeypatch.setattr(moodle_client, "user_exists", lambda user_id: True)
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"


def test_two_moodle_users_with_one_email_are_reported_as_ambiguous(moodle):
    email, headers = _lecturer()
    moodle["users"][email] = [1, 2]

    assert client.get("/moodle/account", headers=headers).json()["status"] == "ambiguous"
    refused = client.post("/moodle/account/link", headers=headers)
    assert refused.status_code == 422 and moodle["created"] == []


def test_yes_links_the_moodle_user_that_already_has_the_email_and_creates_nothing(moodle):
    email, headers = _lecturer()
    moodle["users"][email.title()] = [55]

    response = client.post("/moodle/account/link", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"email": email, "status": "linked", "created": False}
    assert moodle["created"] == []
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"


def test_yes_creates_the_moodle_user_when_there_is_none_and_a_second_yes_does_not_duplicate(moodle):
    email, headers = _lecturer(name="Nguyễn Thị Lan")

    first = client.post("/moodle/account/link", headers=headers)
    second = client.post("/moodle/account/link", headers=headers)

    assert first.json() == {"email": email, "status": "linked", "created": True}
    assert second.json() == {"email": email, "status": "linked", "created": False}
    assert len(moodle["created"]) == 1
    made = moodle["created"][0]
    assert made["email"] == email and (made["firstname"], made["lastname"]) == ("Nguyễn Thị", "Lan")
    assert len(made["password"]) >= 20 and "Lan" not in made["password"]
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"


def test_a_lecturer_whose_email_is_not_edu_gets_no_moodle_account_created(moodle):
    _, headers = _lecturer(domain="gmail.com")

    response = client.post("/moodle/account/link", headers=headers)

    assert response.status_code == 422 and "edu" in response.json()["detail"]
    assert moodle["created"] == []


def test_when_moodle_cannot_be_reached_the_answer_says_so(monkeypatch):
    _, headers = _lecturer()

    def down(emails):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(moodle_client, "find_users_by_email", down)

    assert client.get("/moodle/account", headers=headers).status_code == 502
    assert client.post("/moodle/account/link", headers=headers).status_code == 502


def test_an_admin_has_no_moodle_account_to_link_and_a_stranger_is_turned_away(moodle):
    login = create_and_login(client, f"adm-{uuid.uuid4().hex[:8]}@example.edu", role=UserRole.admin)
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    assert client.get("/moodle/account", headers=headers).status_code == 404
    assert client.post("/moodle/account/link", headers=headers).status_code == 404
    assert client.post("/moodle/account/prompt/dismiss", headers=headers).status_code == 404
    assert client.get("/moodle/account").status_code == 401
    assert client.post("/moodle/account/prompt/dismiss").status_code == 401
    assert client.post("/moodle/account/link").status_code == 401


def test_a_course_cannot_be_linked_or_created_in_moodle_before_the_account_is(moodle):
    email, headers = _lecturer()
    moodle["users"][email] = [55]
    course_id = client.post("/courses", json={"name": "Lập trình"}, headers=headers).json()["id"]
    moodle_id = 1_000 + uuid.uuid4().int % 10**9  # unique: the claims table outlives the run

    linking = client.post(f"/courses/{course_id}/link-moodle", json={"moodle_course_id": moodle_id}, headers=headers)
    creating = client.post(f"/courses/{course_id}/create-moodle", headers=headers)

    for refused in (linking, creating):
        assert refused.status_code == 422
        assert "Liên kết tài khoản Moodle" in refused.json()["detail"]

    client.post("/moodle/account/link", headers=headers)
    linked = client.post(f"/courses/{course_id}/link-moodle", json={"moodle_course_id": moodle_id}, headers=headers)
    assert linked.status_code == 200
