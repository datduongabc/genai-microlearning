"""Rate limits: login attempts are limited per client address, and the endpoints that cost
money (model calls, Moodle courses, uploads) per user, so people behind one address do not
share a bucket. The endpoint tests aim at courses that do not exist: the request is
counted and then answered 404, so no model call or Moodle request is ever made. Every call
uses a different id on purpose: a limit that counted per URL would never trip."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Course, User, UserRole
from tests.auth_helpers import STRONG_PASSWORD

pytestmark = pytest.mark.rate_limits

client = TestClient(app)


@pytest.fixture
def two_users():
    db = SessionLocal()
    try:
        users = [
            User(email=f"rate-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", role=UserRole.teacher)
            for _ in range(2)
        ]
        db.add_all(users)
        db.commit()
        ids = [u.id for u in users]
    finally:
        db.close()

    yield [{"Authorization": f"Bearer {create_access_token(i)}"} for i in ids]

    db = SessionLocal()
    try:
        db.query(Course).filter(Course.owner_id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _codes(times: int, send) -> list[int]:
    return [send().status_code for _ in range(times)]


def test_the_eleventh_login_attempt_in_a_minute_is_refused():
    # A different email each time: the limit is per address, not per account.
    run = uuid.uuid4().hex[:8]
    emails = iter(f"ratelimit-{run}-{i}@example.com" for i in range(11))

    def attempt():
        return client.post("/auth/login", json={"email": next(emails), "password": STRONG_PASSWORD})

    codes = _codes(11, attempt)

    assert codes[:10] == [401] * 10
    assert codes[10] == 429


def test_one_email_tried_over_and_over_is_slowed_by_the_address_limit_too():
    email = f"ratelimit-same-{uuid.uuid4().hex[:8]}@example.com"

    codes = _codes(11, lambda: client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD}))

    assert codes[:10] == [401] * 10
    assert codes[10] == 429


def test_sign_ups_are_limited_too():
    email = f"rate-signup-{uuid.uuid4().hex[:8]}@example.com"

    def attempt():
        # Refused because it is too short, but the request reached the endpoint, so it counts.
        return client.post("/auth/register", json={"email": email, "name": "Sign Up", "password": "ngan"})

    codes = _codes(11, attempt)

    assert codes[:10] == [422] * 10
    assert codes[10] == 429


CASES = [
    ("chat", 20, lambda h: client.post(f"/courses/{uuid.uuid4()}/chat", json={"message": "hi"}, headers=h)),
    ("generate", 10, lambda h: client.post("/modules/generate", params={"document_id": str(uuid.uuid4())}, headers=h)),
    ("create-moodle", 5, lambda h: client.post(f"/courses/{uuid.uuid4()}/create-moodle", headers=h)),
    ("publish", 20, lambda h: client.post(f"/modules/{uuid.uuid4()}/publish-to-moodle", json={"section_num": 0}, headers=h)),
    ("upload", 20, lambda h: client.post(
        "/documents", params={"course_id": str(uuid.uuid4())}, files={"file": ("a.txt", b"x", "text/plain")}, headers=h
    )),
    ("create-course", 30, lambda h: client.post("/courses", json={"name": "c"}, headers=h)),
]


@pytest.mark.parametrize("name, allowed, call", CASES, ids=[c[0] for c in CASES])
def test_a_costly_endpoint_stops_answering_one_user_after_its_limit(two_users, name, allowed, call):
    mine, _ = two_users

    codes = _codes(allowed + 1, lambda: call(mine))

    assert 429 not in codes[:allowed]
    assert codes[allowed] == 429


def test_one_users_limit_does_not_use_up_another_users(two_users):
    heavy, other = two_users
    _codes(31, lambda: client.post("/courses", json={"name": "c"}, headers=heavy))

    assert client.post("/courses", json={"name": "c"}, headers=heavy).status_code == 429
    assert client.post("/courses", json={"name": "c"}, headers=other).status_code == 200


def test_a_refused_request_is_logged_with_who_it_was_and_which_path(two_users, caplog):
    caplog.set_level(logging.WARNING)
    mine, _ = two_users
    _codes(6, lambda: client.post(f"/courses/{uuid.uuid4()}/create-moodle", headers=mine))

    lines = [r.getMessage() for r in caplog.records if "rate_limited" in r.getMessage()]

    assert len(lines) == 1
    assert "/create-moodle" in lines[0]
    assert "user:" in lines[0]


def test_a_refused_request_says_how_to_retry(two_users):
    mine, _ = two_users
    _codes(5, lambda: client.post(f"/courses/{uuid.uuid4()}/create-moodle", headers=mine))

    refused = client.post(f"/courses/{uuid.uuid4()}/create-moodle", headers=mine)

    assert refused.status_code == 429
    assert "error" in refused.json()
