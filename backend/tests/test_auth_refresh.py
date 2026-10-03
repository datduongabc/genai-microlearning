"""The basic HTTP contract of the login tokens: what login returns, that a refreshed access token
really works, and that bad tokens are refused. Rotation, expiry and logout of a token are in
test_refresh_sessions.py."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.auth_helpers import STRONG_PASSWORD, make_account

client = TestClient(app)


def _create_and_login(email: str) -> dict:
    make_account(email, name="Test")
    response = client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    return response.json()


def test_login_returns_both_access_and_refresh_token():
    data = _create_and_login(f"refresh-{uuid.uuid4().hex[:8]}@example.com")
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["access_token"] != data["refresh_token"]


def test_refresh_issues_a_new_working_access_token():
    data = _create_and_login(f"refresh-{uuid.uuid4().hex[:8]}@example.com")

    refresh_resp = client.post("/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert refresh_resp.status_code == 200
    new_tokens = refresh_resp.json()
    assert new_tokens["access_token"]
    assert new_tokens["refresh_token"]

    # Access token mới phải dùng được thật, không chỉ có mặt trong response.
    me_resp = client.get("/courses", headers={"Authorization": f"Bearer {new_tokens['access_token']}"})
    assert me_resp.status_code == 200


def test_refresh_with_invalid_token_is_rejected():
    resp = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert resp.status_code == 401


def test_logout_with_unknown_token_is_a_safe_noop():
    resp = client.post("/auth/logout", json={"refresh_token": "never-issued-token"})
    assert resp.status_code == 204
