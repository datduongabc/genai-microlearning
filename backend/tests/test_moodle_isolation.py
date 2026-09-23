"""Moodle courses belong to whoever first linked or created them through this app.

The application talks to Moodle with one account that sees everything, so without a rule
of its own any user could list, link and then push into a course that another teacher
set up. A course claimed by one user is invisible to every other: the search leaves it
out and an attempt to link it is answered exactly as for a course that does not exist,
so neither confirms that it is taken. The claim outlives the link, otherwise unlinking
or deleting a course would hand its Moodle course, with the content in it, to the next
person who asked."""

import concurrent.futures
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Course, MoodleCourseClaim, User, UserRole
from app.services import moodle_client

client = TestClient(app)


@pytest.fixture
def users():
    """Two teachers, made straight in the database (these tests are not about signing in)."""
    db = SessionLocal()
    try:
        made = [
            User(email=f"iso-{i}-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", role=UserRole.teacher)
            for i in range(2)
        ]
        db.add_all(made)
        db.commit()
        ids = [u.id for u in made]
    finally:
        db.close()

    yield [{"Authorization": f"Bearer {create_access_token(i)}"} for i in ids]

    db = SessionLocal()
    try:
        db.query(MoodleCourseClaim).filter(MoodleCourseClaim.owner_id.in_(ids)).delete(synchronize_session=False)
        db.query(Course).filter(Course.owner_id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _moodle_id() -> int:
    return 1_000 + uuid.uuid4().int % 1_000_000_000


def _course(headers) -> str:
    return client.post("/courses", json={"name": "c"}, headers=headers).json()["id"]


def _link(headers, course_id: str, moodle_id: int):
    return client.post(f"/courses/{course_id}/link-moodle", json={"moodle_course_id": moodle_id}, headers=headers)


def _search(headers, monkeypatch, *shown: int) -> list[int]:
    monkeypatch.setattr(
        moodle_client, "search_courses",
        lambda query, limit=20: [{"id": i, "fullname": f"Course {i}", "shortname": f"C{i}"} for i in shown],
    )
    return [c["id"] for c in client.get("/courses/moodle/search", params={"q": "x"}, headers=headers).json()]


def _nothing_exists_in_moodle(monkeypatch):
    monkeypatch.setattr(moodle_client, "get_course", lambda course_id: None)


def test_the_search_leaves_out_a_course_another_user_has_linked(users, monkeypatch):
    owner, stranger = users
    taken, free = _moodle_id(), _moodle_id()
    _link(owner, _course(owner), taken)

    assert _search(stranger, monkeypatch, taken, free) == [free]


def test_the_search_still_leaves_it_out_after_its_owner_unlinked_or_deleted_the_course(users, monkeypatch):
    owner, stranger = users
    unlinked, deleted = _moodle_id(), _moodle_id()
    first, second = _course(owner), _course(owner)
    _link(owner, first, unlinked)
    _link(owner, second, deleted)
    client.delete(f"/courses/{first}/link-moodle", headers=owner)
    client.delete(f"/courses/{second}", headers=owner)

    assert _search(stranger, monkeypatch, unlinked, deleted) == []


def test_the_owner_still_finds_their_own_course_once_it_is_unlinked(users, monkeypatch):
    owner, _ = users
    mine = _moodle_id()
    course = _course(owner)
    _link(owner, course, mine)
    client.delete(f"/courses/{course}/link-moodle", headers=owner)

    assert _search(owner, monkeypatch, mine) == [mine]


def test_linking_a_course_another_user_holds_is_answered_like_one_that_does_not_exist(users, monkeypatch):
    owner, stranger = users
    taken, missing = _moodle_id(), _moodle_id()
    _link(owner, _course(owner), taken)
    real = moodle_client.get_course
    monkeypatch.setattr(moodle_client, "get_course", lambda course_id: None if course_id == missing else real(course_id))
    mine = _course(stranger)

    against_taken = _link(stranger, mine, taken)
    against_missing = _link(stranger, mine, missing)

    assert against_taken.status_code == 404
    assert (against_taken.status_code, against_taken.json()) == (against_missing.status_code, against_missing.json())
    courses = client.get("/courses", headers=stranger).json()
    assert [c["moodle_course_id"] for c in courses] == [None]


def test_the_claim_outlives_the_link_so_the_next_person_cannot_take_the_course_over(users):
    owner, stranger = users
    held = _moodle_id()
    course = _course(owner)
    _link(owner, course, held)
    client.delete(f"/courses/{course}", headers=owner)

    assert _link(stranger, _course(stranger), held).status_code == 404
    assert _link(owner, _course(owner), held).status_code == 200


def test_a_course_created_through_the_app_is_claimed_by_its_creator(users, monkeypatch):
    owner, stranger = users
    created = _moodle_id()
    monkeypatch.setattr(moodle_client, "create_course", lambda name: created)
    monkeypatch.setattr(moodle_client, "enrol_as_teacher", lambda course_id: None)
    client.post(f"/courses/{_course(owner)}/create-moodle", headers=owner)

    assert _link(stranger, _course(stranger), created).status_code == 404
    assert _search(stranger, monkeypatch, created) == []


def test_linking_a_course_that_is_not_in_moodle_is_refused_and_claims_nothing(users, monkeypatch):
    owner, _ = users
    _nothing_exists_in_moodle(monkeypatch)
    ghost = _moodle_id()

    response = _link(owner, _course(owner), ghost)

    assert response.status_code == 404
    db = SessionLocal()
    try:
        assert db.get(MoodleCourseClaim, ghost) is None
    finally:
        db.close()


def test_when_moodle_cannot_be_asked_nothing_is_linked_or_claimed(users, monkeypatch):
    owner, _ = users
    moodle_id = _moodle_id()

    def unreachable(course_id):
        raise moodle_client.MoodleError("core_course_get_courses: unreachable")

    monkeypatch.setattr(moodle_client, "get_course", unreachable)
    course = _course(owner)

    response = _link(owner, course, moodle_id)

    assert response.status_code == 502
    assert client.get("/courses", headers=owner).json()[0]["moodle_course_id"] is None
    db = SessionLocal()
    try:
        assert db.get(MoodleCourseClaim, moodle_id) is None
    finally:
        db.close()


def test_two_users_linking_the_same_free_course_at_once_leave_exactly_one_holder(users):
    first, second = users
    contested = _moodle_id()
    courses = [_course(first), _course(second)]

    def link(pair):
        headers, course_id = pair
        return TestClient(app).post(
            f"/courses/{course_id}/link-moodle", json={"moodle_course_id": contested}, headers=headers
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(link, [(first, courses[0]), (second, courses[1])]))

    assert sorted(codes) == [200, 404]


def test_one_user_cannot_link_the_same_moodle_course_to_two_of_their_own_courses(users):
    owner, _ = users
    moodle_id = _moodle_id()
    _link(owner, _course(owner), moodle_id)

    assert _link(owner, _course(owner), moodle_id).status_code == 409
