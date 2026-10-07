"""A lecturer logs in with their Moodle username and password. Moodle is where lecturer accounts and
passwords live: the first successful login makes the local lecturer account already linked to that
Moodle user (or links an existing local account with the same email), the next ones reuse it, a locked
lecturer is refused even with valid credentials, a Moodle email that is not an edu address is refused,
and the Moodle password is never stored or logged. Admins keep a local password. Moodle itself is
replaced by a stand-in."""

import concurrent.futures
import logging
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.db import SessionLocal, engine
from app.main import app
from app.models import Course, MoodleCourseClaim, RefreshToken, User, UserRole
from app.moodle import client as moodle_client
from tests.auth_helpers import STRONG_PASSWORD, create_and_login, make_account

client = TestClient(app)

MOODLE_PASSWORD = "Moodle-Sentinel-pw-91"
PREFIX = "mdl-"


@pytest.fixture(autouse=True)
def remove_created_users():
    yield
    with SessionLocal() as db:
        ids = [row.id for row in db.query(User.id).filter(User.email.like(f"{PREFIX}%"))]
        db.query(RefreshToken).filter(RefreshToken.user_id.in_(ids)).delete(synchronize_session=False)
        db.query(MoodleCourseClaim).filter(MoodleCourseClaim.owner_id.in_(ids)).delete(synchronize_session=False)
        db.query(Course).filter(Course.owner_id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.commit()


@pytest.fixture
def moodle(monkeypatch):
    """The Moodle users, {username: {password, id, email, name}}, and the calls the app made."""
    state = {"users": {}, "checked": [], "gone": set()}

    def verify_login(username, password):
        state["checked"].append((username, password))
        known = state["users"].get(username.lower())
        return known["id"] if known and known["password"] == password else None

    def get_user(user_id):
        for known in state["users"].values():
            if known["id"] == user_id and user_id not in state["gone"]:
                return {"id": user_id, "email": known["email"].strip().lower(), "name": known["name"]}
        return None

    def forbidden(*args, **kwargs):
        raise AssertionError("the app must not call any other Moodle function at login")

    monkeypatch.setattr(moodle_client, "verify_login", verify_login)
    monkeypatch.setattr(moodle_client, "get_user", get_user)
    monkeypatch.setattr(moodle_client, "user_exists", lambda user_id: get_user(user_id) is not None)
    monkeypatch.setattr(moodle_client, "call", forbidden)
    return state


def _new_moodle_user(moodle, username=None, email=None, name="Nguyễn Thị Lan", password=MOODLE_PASSWORD):
    username = username or f"{PREFIX}{uuid.uuid4().hex[:10]}"
    # Unique per run: the test database outlives it, and a Moodle user links to one lecturer.
    moodle["users"][username] = {
        "password": password, "id": 10_000 + uuid.uuid4().int % 10**9,
        "email": email if email is not None else f"{username}@hcmut.edu.vn", "name": name,
    }
    return username, moodle["users"][username]


def _login(username, password=MOODLE_PASSWORD):
    return client.post("/auth/login", json={"username": username, "password": password})


def _row(email):
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email.lower()).first()
        if user is not None:
            db.expunge(user)
        return user


def _headers(login):
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_the_first_login_makes_the_lecturer_already_linked_and_the_token_works(moodle):
    username, known = _new_moodle_user(moodle)

    response = _login(username)

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == known["email"] and body["user"]["role"] == "teacher"
    assert body["user"]["name"] == "Nguyễn Thị Lan"
    user = _row(known["email"])
    assert user.moodle_user_id == known["id"] and user.is_active is True and user.role == UserRole.teacher
    assert client.get("/courses", headers=_headers(response)).status_code == 200
    assert client.get("/moodle/account", headers=_headers(response)).json() == {
        "email": known["email"], "status": "linked",
    }
    assert moodle["checked"] == [(username, MOODLE_PASSWORD)]


def test_the_second_login_reuses_the_account(moodle):
    username, known = _new_moodle_user(moodle)
    first = _login(username)

    second = _login(username)

    assert second.status_code == 200
    assert second.json()["user"]["id"] == first.json()["user"]["id"]
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == known["email"]).count() == 1


def test_the_second_login_finds_the_lecturer_by_the_moodle_account_even_when_their_moodle_email_changed(moodle):
    username, known = _new_moodle_user(moodle)
    first = _login(username)
    known["email"] = f"{PREFIX}changed-{uuid.uuid4().hex[:6]}@hcmut.edu.vn"

    second = _login(username)

    assert second.status_code == 200 and second.json()["user"]["id"] == first.json()["user"]["id"]


def test_a_wrong_password_and_an_unknown_user_are_refused_the_same_way_and_make_nothing(moodle):
    username, known = _new_moodle_user(moodle)

    wrong = _login(username, "not-the-password")
    unknown = _login("nobody.at.all")
    empty = _login(username, "")

    assert {wrong.status_code, unknown.status_code, empty.status_code} == {401}
    assert wrong.json() == unknown.json() == empty.json()
    assert _row(known["email"]) is None
    assert moodle["checked"][-1][0] == "nobody.at.all"  # the empty password never reached Moodle


@pytest.mark.parametrize("email", ["lan@gmail.com", "lan@example.com", "lan@edu.com", "", "lan@x.edu.com"])
def test_a_moodle_email_that_is_not_an_edu_address_is_refused_and_nothing_is_made(moodle, email):
    username, known = _new_moodle_user(moodle, email=email)

    response = _login(username)

    assert response.status_code == 403
    with SessionLocal() as db:
        assert db.query(User).filter(User.moodle_user_id == known["id"]).count() == 0


@pytest.mark.parametrize("domain", ["hcmut.edu.vn", "x.edu", "Mail.HCMUT.EDU.VN"])
def test_edu_emails_of_schools_are_accepted_whatever_their_case(moodle, domain):
    username, known = _new_moodle_user(moodle, email=f"{PREFIX}{uuid.uuid4().hex[:8]}@{domain}")

    assert _login(username).status_code == 200
    assert _row(known["email"]).email == known["email"].lower()


def test_an_existing_local_lecturer_with_the_same_email_is_linked_not_duplicated(moodle):
    username, known = _new_moodle_user(moodle, email=f"{PREFIX}{uuid.uuid4().hex[:8]}@hcmut.edu.vn")
    old = make_account(known["email"].upper(), STRONG_PASSWORD, name="Old Name")
    assert old.moodle_user_id is None

    response = _login(username)

    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(old.id)
    assert _row(known["email"]).moodle_user_id == known["id"]
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == known["email"].lower()).count() == 1


def test_a_local_lecturer_cannot_log_in_with_the_old_local_password_any_more(moodle):
    email = f"{PREFIX}{uuid.uuid4().hex[:8]}@hcmut.edu.vn"
    make_account(email, STRONG_PASSWORD)

    response = _login(email, STRONG_PASSWORD)

    assert response.status_code == 401


def test_an_existing_account_linked_to_another_live_moodle_user_is_not_taken_over(moodle):
    first_name, first = _new_moodle_user(moodle, email=f"{PREFIX}{uuid.uuid4().hex[:8]}@hcmut.edu.vn")
    assert _login(first_name).status_code == 200
    second_name, _ = _new_moodle_user(moodle, email=first["email"])

    refused = _login(second_name)

    assert refused.status_code == 409
    assert _row(first["email"]).moodle_user_id == first["id"]


def test_an_account_linked_to_a_moodle_user_that_is_gone_is_linked_to_the_new_one(moodle):
    first_name, first = _new_moodle_user(moodle, email=f"{PREFIX}{uuid.uuid4().hex[:8]}@hcmut.edu.vn")
    original = _login(first_name)
    moodle["gone"].add(first["id"])
    second_name, second = _new_moodle_user(moodle, email=first["email"])

    again = _login(second_name)

    assert again.status_code == 200 and again.json()["user"]["id"] == original.json()["user"]["id"]
    assert _row(first["email"]).moodle_user_id == second["id"]


def test_a_moodle_login_never_opens_an_admin_account_with_the_same_email(moodle):
    email = f"{PREFIX}{uuid.uuid4().hex[:8]}@hcmut.edu.vn"
    admin = make_account(email, STRONG_PASSWORD, role=UserRole.admin)
    # The Moodle user's username is not the admin's email, so the login goes through Moodle.
    username, known = _new_moodle_user(moodle, username=f"{PREFIX}other-{uuid.uuid4().hex[:6]}", email=email)

    response = _login(username)

    assert response.status_code == 403
    assert _row(email).moodle_user_id is None and _row(email).id == admin.id
    assert "access_token" not in response.text and known["id"]


def test_a_locked_lecturer_is_refused_even_with_valid_moodle_credentials(moodle):
    username, known = _new_moodle_user(moodle)
    first = _login(username)
    with SessionLocal() as db:
        db.query(User).filter(User.email == known["email"]).update({"is_active": False})
        db.commit()

    refused = _login(username)
    wrong = _login(username, "not-the-password")

    assert refused.status_code == 403 and "admin" in refused.json()["detail"]
    assert client.get("/courses", headers=_headers(first)).status_code == 401
    # Said only once Moodle accepted the password: a stranger learns nothing about the account.
    assert wrong.status_code == 401


def test_a_locked_account_found_by_email_is_not_linked_by_a_login_that_is_refused(moodle):
    username, known = _new_moodle_user(moodle, email=f"{PREFIX}{uuid.uuid4().hex[:8]}@hcmut.edu.vn")
    locked = make_account(known["email"], STRONG_PASSWORD)
    with SessionLocal() as db:
        db.query(User).filter(User.id == locked.id).update({"is_active": False})
        db.commit()

    assert _login(username).status_code == 403
    assert _row(known["email"]).moodle_user_id is None


def test_when_moodle_cannot_be_reached_a_lecturer_is_told_and_an_admin_still_logs_in(moodle, monkeypatch):
    username, _ = _new_moodle_user(moodle)
    admin_email = f"{PREFIX}admin-{uuid.uuid4().hex[:8]}@hcmut.edu.vn"
    make_account(admin_email, STRONG_PASSWORD, role=UserRole.admin)

    def down(*args):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(moodle_client, "verify_login", down)

    lecturer = _login(username)
    admin = client.post("/auth/login", json={"username": admin_email, "password": STRONG_PASSWORD})

    assert lecturer.status_code == 503
    assert lecturer.json()["detail"].startswith("Moodle tạm thời không phản hồi")
    assert admin.status_code == 200 and admin.json()["user"]["role"] == "admin"


def test_when_moodle_refuses_the_web_service_the_lecturer_is_told_to_contact_the_admin(moodle, monkeypatch):
    username, _ = _new_moodle_user(moodle)

    def refused(*args):
        raise moodle_client.MoodleError("local_lms_bridge_verify_login: Access control exception", "accessexception")

    monkeypatch.setattr(moodle_client, "verify_login", refused)

    response = _login(username)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "quản trị viên" in detail and "verify_login" not in detail and "Access control" not in detail


def test_the_moodle_password_is_never_stored_or_logged(moodle, caplog):
    username, known = _new_moodle_user(moodle)
    caplog.set_level(logging.DEBUG)

    _login(username)
    _login(username, MOODLE_PASSWORD + "-wrong")

    assert MOODLE_PASSWORD not in caplog.text
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == known["email"]).one()
        assert MOODLE_PASSWORD not in repr({c.key: getattr(user, c.key) for c in inspect(User).column_attrs})
    assert not [c for c in inspect(engine).get_columns("users") if "moodle" in c["name"] and "password" in c["name"]]


def test_logins_and_refusals_are_logged_without_the_password(moodle, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    username, known = _new_moodle_user(moodle)

    _login(username, "not-the-password")
    _login(username)
    _login(username)

    lines = [r.getMessage() for r in caplog.records if r.name == "app.audit"]
    assert any("login_failed" in line and "moodle_credentials" in line for line in lines)
    assert any("moodle_account_linked" in line and known["email"] in line and '"created": true' in line for line in lines)
    assert sum("login_success" in line and known["email"] in line for line in lines) == 2
    assert not any("not-the-password" in line or MOODLE_PASSWORD in line for line in lines)


def test_simultaneous_first_logins_make_one_account(moodle):
    username, known = _new_moodle_user(moodle)

    def attempt(_):
        return TestClient(app).post("/auth/login", json={"username": username, "password": MOODLE_PASSWORD}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(attempt, range(6)))

    assert codes == [200] * 6
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == known["email"]).count() == 1


def test_an_admin_logs_in_with_the_local_password_and_never_asks_moodle(moodle):
    email = f"{PREFIX}admin-{uuid.uuid4().hex[:8]}@hcmut.edu.vn"
    make_account(email, STRONG_PASSWORD, role=UserRole.admin)

    ok = client.post("/auth/login", json={"username": email, "password": STRONG_PASSWORD})
    bad = client.post("/auth/login", json={"username": email, "password": "wrong-one"})

    assert ok.status_code == 200 and ok.json()["user"]["role"] == "admin"
    assert bad.status_code == 401
    assert moodle["checked"] == []


def test_a_moodle_user_cannot_get_into_an_admin_account_by_naming_its_email_as_username(moodle):
    email = f"{PREFIX}admin-{uuid.uuid4().hex[:8]}@hcmut.edu.vn"
    make_account(email, STRONG_PASSWORD, role=UserRole.admin)
    # A Moodle user whose username is the admin's email and whose password the admin does not have.
    moodle["users"][email] = {"password": MOODLE_PASSWORD, "id": 777_001, "email": "x@hcmut.edu.vn", "name": "X"}

    assert client.post("/auth/login", json={"username": email, "password": MOODLE_PASSWORD}).status_code == 401


def test_the_status_is_linked_then_not_linked_once_moodle_no_longer_has_the_user(moodle):
    username, known = _new_moodle_user(moodle)
    login = _login(username)

    assert client.get("/moodle/account", headers=_headers(login)).json()["status"] == "linked"
    moodle["gone"].add(known["id"])  # a Moodle admin deleted it

    assert client.get("/moodle/account", headers=_headers(login)).json() == {
        "email": known["email"], "status": "not_linked",
    }
    assert _row(known["email"]).moodle_user_id is None
    course_id = client.post("/courses", json={"name": "Lập trình"}, headers=_headers(login)).json()["id"]
    assert client.post(f"/courses/{course_id}/create-moodle", headers=_headers(login)).status_code == 422


def test_a_stored_link_is_kept_while_moodle_cannot_be_asked(moodle, monkeypatch):
    username, _ = _new_moodle_user(moodle)
    login = _login(username)

    def down(user_id):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(moodle_client, "user_exists", down)
    assert client.get("/moodle/account", headers=_headers(login)).status_code == 502

    monkeypatch.setattr(moodle_client, "user_exists", lambda user_id: True)
    assert client.get("/moodle/account", headers=_headers(login)).json()["status"] == "linked"


def test_the_account_section_is_read_only_there_is_no_link_or_prompt_route():
    paths = app.openapi()["paths"]

    assert "/moodle/account" in paths and set(paths["/moodle/account"]) == {"get"}
    assert not [p for p in paths if p.startswith("/moodle/account/")]
    assert "/auth/register" not in paths


def test_an_admin_has_no_moodle_account_and_a_stranger_is_turned_away():
    admin = create_and_login(client, f"{PREFIX}adm-{uuid.uuid4().hex[:8]}@hcmut.edu.vn", role=UserRole.admin)

    assert client.get("/moodle/account", headers=_headers_of(admin)).status_code == 404
    assert client.get("/moodle/account").status_code == 401


def _headers_of(login: dict) -> dict:
    return {"Authorization": f"Bearer {login['access_token']}"}


def test_the_app_makes_no_moodle_accounts_and_keeps_no_prompt_flag(moodle):
    assert not hasattr(moodle_client, "create_user")
    assert "moodle_prompted_at" not in {c["name"] for c in inspect(engine).get_columns("users")}
    username, _ = _new_moodle_user(moodle)

    # Every Moodle write goes through `call`, which this stand-in forbids: a whole login must not reach it.
    assert _login(username).status_code == 200


def test_a_course_cannot_be_linked_or_created_in_moodle_before_the_account_is_linked():
    login = create_and_login(client, f"{PREFIX}nolink-{uuid.uuid4().hex[:8]}@hcmut.edu.vn")
    headers = _headers_of(login)
    course_id = client.post("/courses", json={"name": "Lập trình"}, headers=headers).json()["id"]
    moodle_id = 1_000 + uuid.uuid4().int % 10**9  # unique: the claims table outlives the run

    linking = client.post(f"/courses/{course_id}/link-moodle", json={"moodle_course_id": moodle_id}, headers=headers)
    creating = client.post(f"/courses/{course_id}/create-moodle", headers=headers)

    for refused in (linking, creating):
        assert refused.status_code == 422
        assert "đăng nhập lại bằng tài khoản Moodle" in refused.json()["detail"]
