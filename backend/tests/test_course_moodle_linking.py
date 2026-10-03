import uuid

from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import User, UserRole
from app.moodle import client as moodle_client

client = TestClient(app)


def _make_authed_user() -> tuple[str, uuid.UUID]:
    """Create a user directly via the DB and mint a token for it, bypassing
    /auth/register+/auth/login entirely -- these tests are about the course/
    Moodle-linking endpoints, not auth, and the auth HTTP endpoints are
    IP-rate-limited (10/min), which the whole suite shares."""
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        user = User(
            email=f"user-{suffix}@example.com", name="Test", hashed_password="x", role=UserRole.teacher
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return create_access_token(user.id), user.id
    finally:
        db.close()


def _make_course(token: str, suffix: str) -> str:
    course = client.post(
        "/courses", json={"name": f"Course {suffix}"}, headers={"Authorization": f"Bearer {token}"}
    ).json()
    return course["id"]


def _fresh_moodle_id() -> int:
    """A Moodle course id is unique across the whole table and the test DB
    persists between runs, so fixed ids would collide on the second run."""
    return 1_000 + uuid.uuid4().int % 1_000_000_000


def test_linking_an_owned_course_sets_its_moodle_course_id():
    token, _ = _make_authed_user()
    course_id = _make_course(token, uuid.uuid4().hex[:8])
    headers = {"Authorization": f"Bearer {token}"}
    moodle_id = _fresh_moodle_id()

    response = client.post(f"/courses/{course_id}/link-moodle", json={"moodle_course_id": moodle_id}, headers=headers)
    assert response.status_code == 200
    assert response.json()["moodle_course_id"] == moodle_id

    courses = client.get("/courses", headers=headers).json()
    linked = next(c for c in courses if c["id"] == course_id)
    assert linked["moodle_course_id"] == moodle_id


def test_cannot_link_another_users_course():
    token_owner, _ = _make_authed_user()
    token_other, _ = _make_authed_user()
    course_id = _make_course(token_owner, uuid.uuid4().hex[:8])

    response = client.post(
        f"/courses/{course_id}/link-moodle",
        json={"moodle_course_id": _fresh_moodle_id()},
        headers={"Authorization": f"Bearer {token_other}"},
    )
    assert response.status_code == 404


def test_linking_an_existing_moodle_course_does_not_touch_its_enrolments(monkeypatch):
    token, _ = _make_authed_user()
    course_id = _make_course(token, uuid.uuid4().hex[:8])
    enrol_calls = []
    monkeypatch.setattr(moodle_client, "enrol_as_teacher", lambda cid, uid: enrol_calls.append((cid, uid)))

    response = client.post(
        f"/courses/{course_id}/link-moodle",
        json={"moodle_course_id": _fresh_moodle_id()},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert enrol_calls == []  # it's someone's existing course; we only link, never enrol


def test_create_moodle_creates_a_new_course_when_not_yet_linked(monkeypatch):
    token, _ = _make_authed_user()
    course_id = _make_course(token, uuid.uuid4().hex[:8])
    headers = {"Authorization": f"Bearer {token}"}
    moodle_id = _fresh_moodle_id()

    monkeypatch.setattr(moodle_client, "create_course", lambda name: moodle_id)
    monkeypatch.setattr(moodle_client, "enrol_as_teacher", lambda cid, uid: None)

    response = client.post(f"/courses/{course_id}/create-moodle", headers=headers)
    assert response.status_code == 200
    assert response.json()["moodle_course_id"] == moodle_id


def test_create_moodle_enrols_the_lecturers_own_moodle_account_as_teacher(monkeypatch):
    token, _ = _make_authed_user()
    course_id = _make_course(token, uuid.uuid4().hex[:8])
    moodle_id = _fresh_moodle_id()
    enrol_calls = []

    monkeypatch.setattr(moodle_client, "create_course", lambda name: moodle_id)
    monkeypatch.setattr(moodle_client, "enrol_as_teacher", lambda cid, uid: enrol_calls.append((cid, uid)))

    response = client.post(f"/courses/{course_id}/create-moodle", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert enrol_calls == [(moodle_id, 4242)]  # 4242: the Moodle account conftest gives every email


def test_create_moodle_keeps_the_link_when_enrolment_fails(monkeypatch):
    token, _ = _make_authed_user()
    course_id = _make_course(token, uuid.uuid4().hex[:8])
    headers = {"Authorization": f"Bearer {token}"}
    moodle_id = _fresh_moodle_id()

    def failing_enrol(cid, uid):
        raise moodle_client.MoodleError("enrol_manual_enrol_users: instance disabled")

    monkeypatch.setattr(moodle_client, "create_course", lambda name: moodle_id)
    monkeypatch.setattr(moodle_client, "enrol_as_teacher", failing_enrol)

    response = client.post(f"/courses/{course_id}/create-moodle", headers=headers)

    assert response.status_code == 502
    assert str(moodle_id) in response.json()["detail"]
    # The Moodle course already exists, so the link must survive: otherwise a
    # retry would create a second, orphaned Moodle course.
    courses = {c["id"]: c["moodle_course_id"] for c in client.get("/courses", headers=headers).json()}
    assert courses[course_id] == moodle_id


def test_create_moodle_refuses_when_already_linked(monkeypatch):
    token, _ = _make_authed_user()
    course_id = _make_course(token, uuid.uuid4().hex[:8])
    headers = {"Authorization": f"Bearer {token}"}
    client.post(f"/courses/{course_id}/link-moodle", json={"moodle_course_id": _fresh_moodle_id()}, headers=headers)

    calls = []
    monkeypatch.setattr(moodle_client, "create_course", lambda name: calls.append(name) or 999)

    response = client.post(f"/courses/{course_id}/create-moodle", headers=headers)
    assert response.status_code == 400
    assert calls == []  # must not call Moodle again once already linked


def test_moodle_sections_requires_the_course_to_be_linked_first():
    token, _ = _make_authed_user()
    course_id = _make_course(token, uuid.uuid4().hex[:8])
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get(f"/courses/{course_id}/moodle-sections", headers=headers)
    assert response.status_code == 400


def test_moodle_sections_returns_the_linked_courses_sections(monkeypatch):
    token, _ = _make_authed_user()
    course_id = _make_course(token, uuid.uuid4().hex[:8])
    headers = {"Authorization": f"Bearer {token}"}
    client.post(f"/courses/{course_id}/link-moodle", json={"moodle_course_id": _fresh_moodle_id()}, headers=headers)

    monkeypatch.setattr(
        moodle_client,
        "get_course_sections",
        lambda moodle_course_id: [{"sectionnum": 0, "name": "General"}, {"sectionnum": 1, "name": "Week 1"}],
    )

    response = client.get(f"/courses/{course_id}/moodle-sections", headers=headers)
    assert response.status_code == 200
    assert response.json() == [{"sectionnum": 0, "name": "General"}, {"sectionnum": 1, "name": "Week 1"}]


def test_the_moodle_course_list_returns_every_course_to_pick_from(monkeypatch):
    token, _ = _make_authed_user()
    headers = {"Authorization": f"Bearer {token}"}
    first, second = _fresh_moodle_id(), _fresh_moodle_id()

    monkeypatch.setattr(
        moodle_client,
        "list_courses",
        lambda: [
            {"id": first, "fullname": "Intro to AI", "shortname": "CO3061"},
            {"id": second, "fullname": "Databases", "shortname": "CO2013"},
        ],
    )

    response = client.get("/courses/moodle/courses", headers=headers)
    assert response.status_code == 200
    assert response.json() == [
        {"id": first, "fullname": "Intro to AI", "shortname": "CO3061"},
        {"id": second, "fullname": "Databases", "shortname": "CO2013"},
    ]


def test_the_moodle_course_list_says_so_when_moodle_cannot_be_reached(monkeypatch):
    import httpx

    token, _ = _make_authed_user()

    def down():
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(moodle_client, "list_courses", down)

    response = client.get("/courses/moodle/courses", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 502


def test_a_moodle_course_can_only_be_linked_to_one_course_here():
    token, _ = _make_authed_user()
    headers = {"Authorization": f"Bearer {token}"}
    first_course_id = _make_course(token, uuid.uuid4().hex[:8])
    second_course_id = _make_course(token, uuid.uuid4().hex[:8])
    moodle_id = _fresh_moodle_id()

    ok = client.post(f"/courses/{first_course_id}/link-moodle", json={"moodle_course_id": moodle_id}, headers=headers)
    assert ok.status_code == 200

    conflict = client.post(
        f"/courses/{second_course_id}/link-moodle", json={"moodle_course_id": moodle_id}, headers=headers
    )
    assert conflict.status_code == 409

    # the first link must be untouched by the failed attempt
    courses = {c["id"]: c["moodle_course_id"] for c in client.get("/courses", headers=headers).json()}
    assert courses[first_course_id] == moodle_id
    assert courses[second_course_id] is None


def test_the_moodle_course_list_excludes_courses_already_linked_elsewhere(monkeypatch):
    token, _ = _make_authed_user()
    headers = {"Authorization": f"Bearer {token}"}
    linked_course_id = _make_course(token, uuid.uuid4().hex[:8])
    linked_id, free_id = _fresh_moodle_id(), _fresh_moodle_id()
    client.post(f"/courses/{linked_course_id}/link-moodle", json={"moodle_course_id": linked_id}, headers=headers)

    monkeypatch.setattr(
        moodle_client,
        "list_courses",
        lambda: [
            {"id": linked_id, "fullname": "Already Linked", "shortname": "AL1"},
            {"id": free_id, "fullname": "Still Free", "shortname": "SF1"},
        ],
    )

    response = client.get("/courses/moodle/courses", headers=headers)
    assert response.status_code == 200
    ids = [c["id"] for c in response.json()]
    assert linked_id not in ids
    assert free_id in ids
