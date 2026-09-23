"""Wrong passwords never lock an account. Guessing is slowed by the per-address rate limit
and the cost of hashing, not by a lock that anybody could set off on somebody else's email."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import RefreshToken, User
from tests.auth_helpers import STRONG_PASSWORD

client = TestClient(app)


@pytest.fixture
def email():
    address = f"nolock-{uuid.uuid4().hex[:10]}@example.com"
    yield address
    db = SessionLocal()
    try:
        ids = [row.id for row in db.query(User.id).filter(User.email == address)]
        db.query(RefreshToken).filter(RefreshToken.user_id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _log_in(address: str, password: str):
    return client.post("/auth/login", json={"email": address, "password": password})


def test_many_wrong_passwords_do_not_lock_the_right_one_out(email):
    client.post("/auth/register", json={"email": email, "name": "No Lock", "password": STRONG_PASSWORD})

    wrong = [_log_in(email, f"Wr0ng!Passw0rd-{i}").status_code for i in range(12)]

    assert wrong == [401] * 12
    assert _log_in(email, STRONG_PASSWORD).status_code == 200


def test_an_email_that_does_not_exist_is_refused_the_same_way_however_often_it_is_tried():
    ghost = f"nolock-ghost-{uuid.uuid4().hex[:8]}@example.com"

    answers = [_log_in(ghost, "Wr0ng!Passw0rd-x") for _ in range(12)]

    assert {r.status_code for r in answers} == {401}
    assert len({r.text for r in answers}) == 1
