"""A login gives a refresh token that works once: refreshing spends it and hands back a
successor. A spent token is refused. The login lasts fourteen days however often it is
refreshed, and logging out refuses the token that was presented."""

import concurrent.futures
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth.security import hash_refresh_token
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import RefreshToken, User
from tests.auth_helpers import create_and_login, start_session

client = TestClient(app)


@pytest.fixture
def account():
    email = f"session-{uuid.uuid4().hex[:10]}@example.com"
    yield email
    db = SessionLocal()
    try:
        ids = [row.id for row in db.query(User.id).filter(User.email == email)]
        db.query(RefreshToken).filter(RefreshToken.user_id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _login(email: str) -> dict:
    return create_and_login(client, email)


def _refresh(token: str):
    return client.post("/auth/refresh", json={"refresh_token": token})


def _logout(token: str):
    return client.post("/auth/logout", json={"refresh_token": token})


def _edit(token: str, **changes) -> None:
    """Changes the stored row of a token, to move it in time without waiting."""
    db = SessionLocal()
    try:
        row = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_refresh_token(token)).one()
        for key, value in changes.items():
            setattr(row, key, value)
        db.commit()
    finally:
        db.close()


def _row(token: str) -> RefreshToken:
    db = SessionLocal()
    try:
        row = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_refresh_token(token)).one()
        db.expunge(row)
        return row
    finally:
        db.close()


def _second_login(email: str) -> str:
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        db.expunge(user)
    return start_session(user)["refresh_token"]


def test_a_refresh_token_works_once_and_its_successor_carries_on(account):
    first = _login(account)["refresh_token"]
    second = _refresh(first)
    third = _refresh(second.json()["refresh_token"])

    assert second.status_code == 200
    assert third.status_code == 200
    assert third.json()["refresh_token"] not in (first, second.json()["refresh_token"])


@pytest.mark.parametrize("spent_for", [timedelta(0), timedelta(hours=1)])
def test_a_spent_token_is_refused_however_late_it_is_replayed_and_the_current_one_still_works(account, spent_for):
    spent = _login(account)["refresh_token"]
    current = _refresh(spent).json()["refresh_token"]
    _edit(spent, revoked_at=datetime.now(UTC) - spent_for)

    replay = _refresh(spent)

    assert replay.status_code == 401
    # A replay is refused and that is all: it does not take the session down with it.
    assert _refresh(current).status_code == 200


def test_simultaneous_refreshes_of_one_token_let_exactly_one_through(account):
    token = _login(account)["refresh_token"]

    def refresh(_):
        return TestClient(app).post("/auth/refresh", json={"refresh_token": token})

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(refresh, range(8)))

    winners = [r for r in responses if r.status_code == 200]
    assert len(winners) == 1
    assert all(r.status_code == 401 for r in responses if r not in winners)
    assert _refresh(winners[0].json()["refresh_token"]).status_code == 200


def test_logging_out_refuses_the_token_that_was_presented(account):
    first = _login(account)["refresh_token"]
    second = _refresh(first).json()["refresh_token"]

    assert _logout(second).status_code == 204

    assert _refresh(second).status_code == 401


def test_two_logins_are_two_independent_sessions(account):
    laptop = _login(account)["refresh_token"]
    phone = _second_login(account)

    assert _logout(laptop).status_code == 204
    assert _refresh(phone).status_code == 200


def test_a_new_login_lasts_fourteen_days(account):
    row = _row(_login(account)["refresh_token"])

    assert timedelta(days=13, hours=23) < row.expires_at - datetime.now(UTC) <= timedelta(days=14)


def test_refreshing_does_not_push_the_end_of_the_login_further_out(account):
    token = _login(account)["refresh_token"]
    ends = datetime.now(UTC) + timedelta(days=1)
    _edit(token, expires_at=ends)

    renewed = _refresh(token).json()["refresh_token"]

    assert _row(renewed).expires_at == ends
    assert _refresh(renewed).status_code == 200


def test_a_token_past_its_expiry_is_refused(account):
    token = _login(account)["refresh_token"]
    _edit(token, expires_at=datetime.now(UTC) - timedelta(seconds=1))

    assert _refresh(token).status_code == 401


def test_the_access_token_lives_fifteen_minutes(account):
    access = _login(account)["access_token"]

    expires = datetime.fromtimestamp(
        jwt.decode(access, settings.signing_key(), algorithms=[settings.jwt_algorithm])["exp"], UTC
    )

    assert timedelta(minutes=14) < expires - datetime.now(UTC) <= timedelta(minutes=15)


def test_login_returns_both_access_and_refresh_token():
    data = _login(f"refresh-{uuid.uuid4().hex[:8]}@example.com")
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["access_token"] != data["refresh_token"]


def test_refresh_issues_a_new_working_access_token():
    data = _login(f"refresh-{uuid.uuid4().hex[:8]}@example.com")

    refresh_resp = _refresh(data["refresh_token"])
    assert refresh_resp.status_code == 200
    new_tokens = refresh_resp.json()
    assert new_tokens["access_token"]
    assert new_tokens["refresh_token"]

    # Access token mới phải dùng được thật, không chỉ có mặt trong response.
    me_resp = client.get("/courses", headers={"Authorization": f"Bearer {new_tokens['access_token']}"})
    assert me_resp.status_code == 200


def test_refresh_with_invalid_token_is_rejected():
    resp = _refresh("not-a-real-token")
    assert resp.status_code == 401


def test_logout_with_unknown_token_is_a_safe_noop():
    resp = _logout("never-issued-token")
    assert resp.status_code == 204
