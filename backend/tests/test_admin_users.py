"""The admin's account management: who can use it, what it does to a lecturer's access, and that
it is logged. Roles and the locked flag are read from the database on every request, so a change
takes effect with the token the person already holds."""

import logging
import uuid

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import admin as admin_api
from app.auth.security import decode_token
from app.db import SessionLocal
from app.main import app
from app.models import Course, MoodleCourseClaim, RefreshToken, User, UserRole
from app.moodle import client as moodle_client
from tests.auth_helpers import STRONG_PASSWORD, create_and_login, make_account

client = TestClient(app)


def _email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.edu"


def _bearer(login: dict) -> dict:
    return {"Authorization": f"Bearer {login['access_token']}"}


@pytest.fixture
def admin():
    return create_and_login(client, _email("admin"), role=UserRole.admin)


@pytest.fixture
def lecturer():
    return create_and_login(client, _email("lecturer"))


@pytest.fixture(autouse=True)
def moodle(monkeypatch):
    """A lecturer logs in through Moodle: this Moodle accepts the usernames in `moodle["users"]`
    ({username: (password, Moodle user id, email)}), and the admin page never asks Moodle anything."""
    state = {"users": {}}

    def verify_login(username, password):
        known = state["users"].get(username)
        return known[1] if known and known[0] == password else None

    def get_user(user_id):
        for _, moodle_id, email in state["users"].values():
            if moodle_id == user_id:
                return {"id": moodle_id, "email": email, "name": "Lan Nguyen"}
        return None

    def asked(*args, **kwargs):
        raise AssertionError("the admin page must not call Moodle")

    monkeypatch.setattr(moodle_client, "verify_login", verify_login)
    monkeypatch.setattr(moodle_client, "get_user", get_user)
    monkeypatch.setattr(moodle_client, "call", asked)
    return state


def _login(email, password=STRONG_PASSWORD):
    """An admin logs in with the email and the local password."""
    return client.post("/auth/login", json={"username": email, "password": password})


def _moodle_login(moodle, lecturer, password="Moodle-pass-1"):
    """The lecturer logs in with their Moodle username, which here is their email."""
    email = lecturer["user"]["email"]
    with SessionLocal() as db:
        moodle_id = db.query(User).filter(User.email == email).one().moodle_user_id or 100_000 + uuid.uuid4().int % 10**8
    moodle["users"][email] = (password, moodle_id, email)
    return client.post("/auth/login", json={"username": email, "password": password})








def test_the_list_shows_every_account_with_its_state(admin):
    email = _email("listed")
    make_account(email)

    users = {u["email"]: u for u in client.get("/admin/users", headers=_bearer(admin)).json()}

    assert users[email]["role"] == "teacher" and users[email]["is_active"] is True
    assert "hashed_password" not in users[email]
    assert any(u["role"] == "admin" for u in users.values())


def test_the_list_says_which_lecturers_have_a_stored_moodle_link(admin):
    linked, unlinked = _email("linked"), _email("unlinked")
    make_account(linked)
    make_account(unlinked)
    with SessionLocal() as db:
        db.query(User).filter(User.email == linked).update({"moodle_user_id": 10_000 + uuid.uuid4().int % 10**8})
        db.commit()

    users = {u["email"]: u for u in client.get("/admin/users", headers=_bearer(admin)).json()}

    # The stored link, not whether Moodle happens to have a user with the same email: a lecturer
    # whose Moodle email differs from the app's is still linked.
    assert users[linked]["moodle_status"] == "linked"
    assert users[unlinked]["moodle_status"] == "not_linked"
    assert all(u["moodle_status"] is None for u in users.values() if u["role"] == "admin")


def test_a_disabled_lecturer_is_locked_out_at_once_including_the_token_they_hold(admin, lecturer, moodle):
    lecturer_id = lecturer["user"]["id"]
    email = lecturer["user"]["email"]
    assert client.get("/courses", headers=_bearer(lecturer)).status_code == 200

    disabled = client.post(f"/admin/users/{lecturer_id}/disable", headers=_bearer(admin))

    assert disabled.status_code == 200 and disabled.json()["is_active"] is False
    assert client.get("/courses", headers=_bearer(lecturer)).status_code == 401
    refresh = client.post("/auth/refresh", json={"refresh_token": lecturer["refresh_token"]})
    assert refresh.status_code == 401
    # Valid Moodle credentials do not get a locked lecturer in.
    login = _moodle_login(moodle, lecturer)
    assert login.status_code == 403
    assert "admin" in login.json()["detail"]
    # Only said once Moodle accepted the password: a stranger learns nothing about the account.
    assert client.post("/auth/login", json={"username": email, "password": "not the password 1"}).status_code == 401


def test_enabling_restores_access_and_disabling_never_deleted_anything(admin, lecturer, moodle):
    lecturer_id = lecturer["user"]["id"]
    client.post("/courses", json={"name": "Keep me"}, headers=_bearer(lecturer))

    client.post(f"/admin/users/{lecturer_id}/disable", headers=_bearer(admin))
    enabled = client.post(f"/admin/users/{lecturer_id}/enable", headers=_bearer(admin))

    assert enabled.status_code == 200 and enabled.json()["is_active"] is True
    again = _moodle_login(moodle, lecturer)
    assert again.status_code == 200
    courses = client.get("/courses", headers=_bearer(again.json())).json()
    assert [c["name"] for c in courses] == ["Keep me"]


def test_an_admin_cannot_disable_themselves(admin):
    response = client.post(f"/admin/users/{admin['user']['id']}/disable", headers=_bearer(admin))

    assert response.status_code == 409
    assert client.get("/admin/users", headers=_bearer(admin)).status_code == 200


def test_the_last_active_admin_cannot_be_disabled_even_by_a_stale_session():
    # Two admins disabling each other at the same moment: the one whose request runs second was
    # authenticated while still active, so the check has to look at the database, not at them.
    first = make_account(_email("admin-a"), role=UserRole.admin)
    second = make_account(_email("admin-b"), role=UserRole.admin)
    with SessionLocal() as db:
        db.query(User).filter(User.role == UserRole.admin, User.id != first.id).update({"is_active": False})
        db.commit()
    try:
        with SessionLocal() as db:
            stale_second = db.get(User, second.id)
            with pytest.raises(HTTPException) as refused:
                admin_api._set_active(None, db, stale_second, first.id, active=False)
        assert refused.value.status_code == 409
    finally:
        with SessionLocal() as db:
            db.query(User).filter(User.role == UserRole.admin).update({"is_active": True})
            db.commit()


def test_an_admin_can_disable_another_admin(admin):
    other = create_and_login(client, _email("admin-other"), role=UserRole.admin)

    response = client.post(f"/admin/users/{other['user']['id']}/disable", headers=_bearer(admin))

    assert response.status_code == 200
    assert client.get("/admin/users", headers=_bearer(other)).status_code == 401




def test_an_unknown_user_is_not_found(admin):
    ghost = uuid.uuid4()

    for action in ("disable", "enable"):
        assert client.post(f"/admin/users/{ghost}/{action}", headers=_bearer(admin)).status_code == 404
    assert client.post(f"/admin/users/{ghost}/password", json={"password": STRONG_PASSWORD}, headers=_bearer(admin)).status_code in (404, 405)


def test_a_lecturer_gets_not_found_from_every_admin_route(lecturer):
    other = str(uuid.uuid4())
    calls = [
        client.get("/admin/users", headers=_bearer(lecturer)),
        client.post(f"/admin/users/{other}/disable", headers=_bearer(lecturer)),
        client.post(f"/admin/users/{other}/enable", headers=_bearer(lecturer)),
        client.get("/admin/jobs/failed", headers=_bearer(lecturer)),
        client.post("/admin/jobs/retry", json={"job_ids": [other]}, headers=_bearer(lecturer)),
    ]

    assert [c.status_code for c in calls] == [404] * len(calls)
    assert len({c.text for c in calls}) == 1  # the same answer for all, nothing about what is there


def test_the_role_is_read_from_the_database_not_from_the_token(admin):
    claims = decode_token(admin["access_token"])
    assert not {"role", "is_admin", "roles"} & set(claims)
    assert client.get("/admin/users", headers=_bearer(admin)).status_code == 200

    with SessionLocal() as db:
        db.query(User).filter(User.id == uuid.UUID(admin["user"]["id"])).update({"role": UserRole.teacher})
        db.commit()

    assert client.get("/admin/users", headers=_bearer(admin)).status_code == 404


def test_the_token_of_an_account_that_was_never_active_or_is_forged_is_refused():
    forged = jwt.encode({"sub": str(uuid.uuid4())}, "not-the-secret", algorithm="HS256")

    assert client.get("/admin/users", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_every_admin_action_is_logged_without_a_secret(admin, lecturer, caplog):
    caplog.set_level(logging.INFO)
    lecturer_id = lecturer["user"]["id"]
    client.post(f"/admin/users/{lecturer_id}/disable", headers=_bearer(admin))
    client.post(f"/admin/users/{lecturer_id}/enable", headers=_bearer(admin))

    lines = [r.getMessage() for r in caplog.records if r.name == "app.audit"]

    for event in ("admin_disable_user", "admin_enable_user"):
        assert any(
            event in line and f"actor_id={admin['user']['id']}" in line and lecturer_id in line for line in lines
        ), event
    everything = " ".join(r.getMessage() for r in caplog.records)
    assert STRONG_PASSWORD not in everything


def test_the_admin_can_neither_create_a_lecturer_nor_set_a_lecturer_password(admin, lecturer):
    """Lecturers come from Moodle and their passwords live there: the routes are gone."""
    body = {"email": _email("x"), "name": "X", "password": STRONG_PASSWORD}
    paths = client.get("/openapi.json").json()["paths"]

    assert "post" not in paths.get("/admin/users", {})
    assert not any(path.endswith("/password") for path in paths)
    assert client.post("/admin/users", json=body, headers=_bearer(admin)).status_code == 405
    assert client.post(
        f"/admin/users/{lecturer['user']['id']}/password", json={"password": STRONG_PASSWORD}, headers=_bearer(admin)
    ).status_code in (404, 405)


def test_disabling_ends_every_login_of_the_account(admin, lecturer):
    client.post(f"/admin/users/{lecturer['user']['id']}/disable", headers=_bearer(admin))

    with SessionLocal() as db:
        live = db.query(RefreshToken).filter(
            RefreshToken.user_id == uuid.UUID(lecturer["user"]["id"]), RefreshToken.revoked_at.is_(None)
        ).count()

    assert live == 0


def _delete(admin, user_id):
    return client.delete(f"/admin/users/{user_id}", headers=_bearer(admin))


def _give_course(owner_id):
    with SessionLocal() as db:
        course = Course(name="Môn của giảng viên", owner_id=uuid.UUID(str(owner_id)))
        db.add(course)
        db.commit()
        return course.id


def _exists(user_id):
    with SessionLocal() as db:
        return db.get(User, uuid.UUID(str(user_id))) is not None


def test_an_admin_deletes_a_lecturer_who_has_no_course_with_their_logins(admin, lecturer):
    lecturer_id = lecturer["user"]["id"]
    assert client.get("/admin/users", headers=_bearer(lecturer)).status_code == 404  # holds a live login

    response = _delete(admin, lecturer_id)

    assert response.status_code == 200
    assert not _exists(lecturer_id)
    assert lecturer["user"]["email"] not in {u["email"] for u in client.get("/admin/users", headers=_bearer(admin)).json()}
    assert client.post("/auth/refresh", json={"refresh_token": lecturer["refresh_token"]}).status_code == 401
    with SessionLocal() as db:
        assert db.query(RefreshToken).filter(RefreshToken.user_id == uuid.UUID(lecturer_id)).count() == 0


def test_a_lecturer_with_a_course_is_refused_with_409_and_nothing_is_lost(admin, lecturer):
    lecturer_id = lecturer["user"]["id"]
    course_id = _give_course(lecturer_id)

    response = _delete(admin, lecturer_id)

    assert response.status_code == 409 and "môn học" in response.json()["detail"]
    assert _exists(lecturer_id)
    with SessionLocal() as db:
        assert db.get(Course, course_id) is not None


def test_a_lecturer_whose_course_was_deleted_can_be_deleted(admin, lecturer):
    lecturer_id = lecturer["user"]["id"]
    course_id = _give_course(lecturer_id)
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == course_id).delete()
        db.commit()

    assert _delete(admin, lecturer_id).status_code == 200


def _give_moodle_claim(owner_id):
    claim = 987_000_000 + uuid.uuid4().int % 1_000_000
    with SessionLocal() as db:
        db.add(MoodleCourseClaim(moodle_course_id=claim, owner_id=uuid.UUID(str(owner_id))))
        db.commit()
    return claim


def test_a_lecturer_holding_a_moodle_course_claim_is_refused_and_keeps_it(admin, lecturer):
    # The app course is gone but the Moodle course they created or linked is still theirs: deleting
    # the account would free it for any other lecturer to link and push into.
    lecturer_id = lecturer["user"]["id"]
    claim = _give_moodle_claim(lecturer_id)

    response = _delete(admin, lecturer_id)

    assert response.status_code == 409 and "Moodle" in response.json()["detail"]
    assert _exists(lecturer_id)
    with SessionLocal() as db:
        assert db.get(MoodleCourseClaim, claim).owner_id == uuid.UUID(lecturer_id)


def test_deleting_does_not_call_moodle(admin, lecturer, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("deleting an account must not call Moodle")

    monkeypatch.setattr(moodle_client, "call", forbidden)

    assert _delete(admin, lecturer["user"]["id"]).status_code == 200


def test_the_list_says_how_many_courses_each_lecturer_has(admin, lecturer):
    lecturer_id = lecturer["user"]["id"]
    empty = make_account(_email("empty"))
    _give_course(lecturer_id)
    _give_course(lecturer_id)

    users = {u["id"]: u for u in client.get("/admin/users", headers=_bearer(admin)).json()}

    assert users[lecturer_id]["course_count"] == 2
    assert users[str(empty.id)]["course_count"] == 0
    assert users[admin["user"]["id"]]["course_count"] is None


def test_the_list_says_how_many_moodle_courses_each_lecturer_has_claimed(admin, lecturer):
    lecturer_id = lecturer["user"]["id"]
    empty = make_account(_email("empty"))
    _give_moodle_claim(lecturer_id)

    users = {u["id"]: u for u in client.get("/admin/users", headers=_bearer(admin)).json()}

    assert users[lecturer_id]["moodle_course_count"] == 1
    assert users[str(empty.id)]["moodle_course_count"] == 0
    assert users[admin["user"]["id"]]["moodle_course_count"] is None


def test_an_admin_cannot_delete_themselves_or_another_admin(admin):
    other = create_and_login(client, _email("admin-other"), role=UserRole.admin)

    own = _delete(admin, admin["user"]["id"])
    another = _delete(admin, other["user"]["id"])

    assert own.status_code == 409 and another.status_code == 409
    assert _exists(admin["user"]["id"]) and _exists(other["user"]["id"])


def test_only_an_admin_can_delete_and_an_unknown_user_is_not_found(admin, lecturer):
    victim = make_account(_email("victim"))

    by_lecturer = _delete(lecturer, victim.id)
    anonymous = client.delete(f"/admin/users/{victim.id}")
    unknown = _delete(admin, uuid.uuid4())

    assert by_lecturer.status_code == 404 and anonymous.status_code == 401 and unknown.status_code == 404
    assert _exists(victim.id)


def test_a_lecturer_cannot_delete_themselves(lecturer):
    assert _delete(lecturer, lecturer["user"]["id"]).status_code == 404
    assert _exists(lecturer["user"]["id"])


def test_a_new_course_racing_the_delete_is_refused_by_the_database(admin, lecturer, monkeypatch):
    # The count saw no course, then the lecturer added one before the delete ran: the foreign key
    # still refuses, and the answer is the same 409 instead of an error.
    lecturer_id = lecturer["user"]["id"]
    monkeypatch.setattr(admin_api, "_course_counts", lambda db: {})
    _give_course(lecturer_id)

    response = _delete(admin, lecturer_id)

    assert response.status_code == 409 and _exists(lecturer_id)


def test_deleting_is_logged_without_a_secret(admin, lecturer, caplog):
    caplog.set_level(logging.INFO)
    lecturer_id = lecturer["user"]["id"]

    _delete(admin, lecturer_id)

    lines = [r.getMessage() for r in caplog.records if r.name == "app.audit"]
    assert any(
        "admin_delete_user" in line and f"actor_id={admin['user']['id']}" in line and lecturer_id in line
        for line in lines
    )
    assert not any("hashed_password" in line or "token" in line for line in lines)
