"""The admin's account management: who can use it, what it does to a lecturer's access, and that
it is logged. Roles and the locked flag are read from the database on every request, so a change
takes effect with the token the person already holds."""

import logging
import threading
import time
import uuid

import httpx
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


def _create(admin, email=None, password=STRONG_PASSWORD, name="Lan Nguyen"):
    return client.post(
        "/admin/users",
        json={"email": email or _email("new"), "name": name, "password": password},
        headers=_bearer(admin),
    )


def _login(email, password=STRONG_PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_an_admin_creates_a_lecturer_who_can_log_in(admin):
    email = _email("made")

    created = _create(admin, email)

    assert created.status_code == 201
    assert created.json()["role"] == "teacher" and created.json()["is_active"] is True
    login = _login(email)
    assert login.status_code == 200 and login.json()["user"]["role"] == "teacher"


@pytest.mark.parametrize("domain", ["gmail.com", "example.com", "edu.com"])
def test_an_account_needs_an_edu_email_and_nothing_is_created_otherwise(admin, domain):
    email = f"{uuid.uuid4().hex[:10]}@{domain}"

    refused = _create(admin, email)

    assert refused.status_code == 422 and "edu" in refused.json()["detail"]
    assert _login(email).status_code == 401
    assert email not in {u["email"] for u in client.get("/admin/users", headers=_bearer(admin)).json()}


def test_an_edu_email_of_a_vietnamese_school_is_accepted(admin):
    assert _create(admin, f"{uuid.uuid4().hex[:10]}@hcmut.edu.vn").status_code == 201


def test_the_list_shows_every_account_with_its_state(admin):
    email = _email("listed")
    _create(admin, email)

    users = {u["email"]: u for u in client.get("/admin/users", headers=_bearer(admin)).json()}

    assert users[email]["role"] == "teacher" and users[email]["is_active"] is True
    assert "hashed_password" not in users[email]
    assert any(u["role"] == "admin" for u in users.values())


def test_the_list_says_which_lecturers_have_a_moodle_account_by_email(admin, monkeypatch):
    linked, missing, twice = _email("linked"), _email("missing"), _email("twice")
    for email in (linked, missing, twice):
        make_account(email)

    def fake(emails):
        by_email = {linked: [5], twice: [6, 7]}
        # Moodle holds the address in whatever case it was typed.
        return {e.lower(): by_email.get(e.lower(), []) for e in emails}

    monkeypatch.setattr(moodle_client, "find_users_by_email", fake)

    users = {u["email"]: u for u in client.get("/admin/users", headers=_bearer(admin)).json()}

    assert users[linked]["moodle_status"] == "linked"
    assert users[missing]["moodle_status"] == "not_found"
    assert users[twice]["moodle_status"] == "ambiguous"
    assert all(u["moodle_status"] is None for u in users.values() if u["role"] == "admin")


def test_when_moodle_cannot_be_reached_the_status_is_unknown_and_the_list_still_works(admin, monkeypatch):
    email = _email("offline")
    make_account(email)

    def down(emails):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(moodle_client, "find_users_by_email", down)

    response = client.get("/admin/users", headers=_bearer(admin))

    assert response.status_code == 200
    assert {u["email"]: u for u in response.json()}[email]["moodle_status"] == "unknown"


def test_a_slow_moodle_lookup_is_unknown_and_does_not_hold_up_the_list(admin, monkeypatch):
    slow, quick = _email("slow"), _email("quick")
    for email in (slow, quick):
        make_account(email)
    release = threading.Event()

    def lookup(emails):
        if slow in emails:
            release.wait(5)
        return {e.lower(): [5] for e in emails}

    monkeypatch.setattr(moodle_client, "find_users_by_email", lookup)
    monkeypatch.setattr(admin_api, "MOODLE_LOOKUP_SECONDS", 0.5)

    started = time.monotonic()
    response = client.get("/admin/users", headers=_bearer(admin))
    elapsed = time.monotonic() - started
    release.set()

    assert response.status_code == 200 and elapsed < 4
    users = {u["email"]: u for u in response.json()}
    assert users[slow]["moodle_status"] == "unknown"
    assert users[quick]["moodle_status"] == "linked"


def test_an_email_that_is_taken_or_a_weak_password_is_refused(admin):
    email = _email("dup")
    assert _create(admin, email).status_code == 201

    assert _create(admin, email.upper()).status_code == 409
    assert _create(admin, password="short").status_code == 422
    assert _create(admin, email="not-an-email").status_code == 422


def test_a_disabled_lecturer_is_locked_out_at_once_including_the_token_they_hold(admin, lecturer):
    lecturer_id = lecturer["user"]["id"]
    email = lecturer["user"]["email"]
    assert client.get("/courses", headers=_bearer(lecturer)).status_code == 200

    disabled = client.post(f"/admin/users/{lecturer_id}/disable", headers=_bearer(admin))

    assert disabled.status_code == 200 and disabled.json()["is_active"] is False
    assert client.get("/courses", headers=_bearer(lecturer)).status_code == 401
    refresh = client.post("/auth/refresh", json={"refresh_token": lecturer["refresh_token"]})
    assert refresh.status_code == 401
    login = _login(email)
    assert login.status_code == 403
    assert "admin" in login.json()["detail"]
    # Only said once the password was right: a stranger learns nothing about the account.
    assert _login(email, "not the password 1").status_code == 401


def test_enabling_restores_access_and_disabling_never_deleted_anything(admin, lecturer):
    lecturer_id, email = lecturer["user"]["id"], lecturer["user"]["email"]
    client.post("/courses", json={"name": "Keep me"}, headers=_bearer(lecturer))

    client.post(f"/admin/users/{lecturer_id}/disable", headers=_bearer(admin))
    enabled = client.post(f"/admin/users/{lecturer_id}/enable", headers=_bearer(admin))

    assert enabled.status_code == 200 and enabled.json()["is_active"] is True
    again = _login(email)
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


def test_a_reset_password_replaces_the_old_one_and_ends_the_old_logins(admin, lecturer):
    lecturer_id, email = lecturer["user"]["id"], lecturer["user"]["email"]

    reset = client.post(
        f"/admin/users/{lecturer_id}/password", json={"password": "A brand new long phrase"}, headers=_bearer(admin)
    )

    assert reset.status_code == 200
    assert _login(email).status_code == 401
    assert _login(email, "A brand new long phrase").status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": lecturer["refresh_token"]}).status_code == 401


def test_an_unknown_user_is_not_found(admin):
    ghost = uuid.uuid4()

    for action in ("disable", "enable"):
        assert client.post(f"/admin/users/{ghost}/{action}", headers=_bearer(admin)).status_code == 404
    assert client.post(f"/admin/users/{ghost}/password", json={"password": STRONG_PASSWORD}, headers=_bearer(admin)).status_code == 404


def test_a_lecturer_gets_not_found_from_every_admin_route(lecturer):
    other = str(uuid.uuid4())
    calls = [
        client.get("/admin/users", headers=_bearer(lecturer)),
        client.post("/admin/users", json={"email": _email("x"), "name": "X", "password": STRONG_PASSWORD}, headers=_bearer(lecturer)),
        client.post(f"/admin/users/{other}/disable", headers=_bearer(lecturer)),
        client.post(f"/admin/users/{other}/enable", headers=_bearer(lecturer)),
        client.post(f"/admin/users/{other}/password", json={"password": STRONG_PASSWORD}, headers=_bearer(lecturer)),
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
    password = "A brand new long phrase"
    created = _create(admin, password="Zq8#rW5!kLp2Hy").json()
    client.post(f"/admin/users/{lecturer_id}/disable", headers=_bearer(admin))
    client.post(f"/admin/users/{lecturer_id}/enable", headers=_bearer(admin))
    client.post(f"/admin/users/{lecturer_id}/password", json={"password": password}, headers=_bearer(admin))

    lines = [r.getMessage() for r in caplog.records if r.name == "app.audit"]

    for event, target in (
        ("admin_create_user", created["id"]), ("admin_disable_user", lecturer_id),
        ("admin_enable_user", lecturer_id), ("admin_reset_password", lecturer_id),
    ):
        assert any(event in line and f"actor_id={admin['user']['id']}" in line and target in line for line in lines), event
    everything = " ".join(r.getMessage() for r in caplog.records)
    assert password not in everything and "Zq8#rW5!kLp2Hy" not in everything


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
    assert _login(lecturer["user"]["email"]).status_code == 401
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
    assert _login(lecturer["user"]["email"]).status_code == 200


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

    monkeypatch.setattr(moodle_client, "find_users_by_email", forbidden)

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
