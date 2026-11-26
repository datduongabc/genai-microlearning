"""A protected endpoint accepts a token only when it is signed with the server's key
and algorithm, has an expiry that has not passed, and names an existing user by a
valid user id. Everything else is a plain 401 - never a 5xx, never accepted."""

import base64
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import User

client = TestClient(app)


@pytest.fixture(scope="module")
def user_id():
    db = SessionLocal()
    try:
        user = User(
            email=f"jwt-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False
        )
        db.add(user)
        db.commit()
        created = user.id
    finally:
        db.close()

    yield created

    db = SessionLocal()
    try:
        db.query(User).filter(User.id == created).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _status(token: str) -> int:
    return client.get("/courses", headers={"Authorization": f"Bearer {token}"}).status_code


def _sign(claims: dict, key: str | None = None, algorithm: str | None = None) -> str:
    return jwt.encode(claims, key or settings.signing_key(), algorithm=algorithm or settings.jwt_algorithm)


def _in_an_hour() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def _unsigned(user_id) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part({'sub': str(user_id), 'exp': int(time.time()) + 3600})}."


def test_a_correctly_signed_unexpired_token_of_an_existing_user_is_accepted(user_id):
    assert _status(_sign({"sub": str(user_id), "exp": _in_an_hour()})) == 200


# The CI key is shorter than PyJWT recommends for SHA-512; signing with the server's
# own key is the whole point of wrong-algorithm (the algorithm, not the key, is what is wrong).
@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
@pytest.mark.parametrize(
    "build_token",
    [
        lambda uid: _sign({"sub": str(uid), "exp": _in_an_hour()}, key="a-different-key-" + "x" * 32),
        _unsigned,
        lambda uid: _sign({"sub": str(uid), "exp": _in_an_hour()}, algorithm="HS512"),
        lambda uid: _sign({"sub": str(uid), "exp": datetime.now(UTC) - timedelta(seconds=5)}),
        lambda uid: _sign({"sub": str(uid)}),
    ],
    ids=["wrong-key", "unsigned-alg-none", "wrong-algorithm", "expired", "missing-exp"],
)
def test_tokens_failing_signature_or_expiry_claims_are_refused(user_id, build_token):
    assert _status(build_token(user_id)) == 401


def test_a_token_for_a_user_that_does_not_exist_is_refused():
    assert _status(_sign({"sub": str(uuid.uuid4()), "exp": _in_an_hour()})) == 401


@pytest.mark.parametrize(
    "subject",
    ["not-a-uuid", "", 12345, ["a", "b"], {"id": "x"}, None, "0" * 500],
    ids=["text", "empty", "integer", "list", "object", "null", "very-long"],
)
def test_a_token_whose_subject_is_not_a_user_id_is_a_401_not_a_server_error(subject):
    assert _status(_sign({"sub": subject, "exp": _in_an_hour()})) == 401


def test_a_token_without_a_subject_is_refused():
    assert _status(_sign({"exp": _in_an_hour()})) == 401


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "....", "Bearer", "\x00", "x" * 10_000])
def test_malformed_tokens_are_refused(garbage):
    assert _status(garbage) == 401


def test_a_request_without_credentials_is_refused():
    assert client.get("/courses").status_code == 401


def test_credentials_with_the_wrong_scheme_are_refused(user_id):
    token = _sign({"sub": str(user_id), "exp": _in_an_hour()})

    assert client.get("/courses", headers={"Authorization": f"Basic {token}"}).status_code == 401
