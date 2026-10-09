"""Authentication events go to the application log: what happened, to which account, from
where. The log never holds a secret, and text supplied by the client cannot forge a line."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import RefreshToken, User
from tests.auth_helpers import STRONG_PASSWORD, make_account

client = TestClient(app)


@pytest.fixture
def email():
    address = f"audit-{uuid.uuid4().hex[:10]}@example.com"
    yield address
    db = SessionLocal()
    try:
        ids = [row.id for row in db.query(User.id).filter(User.email == address)]
        db.query(RefreshToken).filter(RefreshToken.user_id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _sign_up(address: str, password: str = STRONG_PASSWORD):
    # A local account whose login audit events are tested.
    return make_account(address, password, name="Audit User", is_admin=True)


def _log_in(address: str, password: str = STRONG_PASSWORD, **kwargs):
    return client.post("/auth/login", json={"email": address, "password": password}, **kwargs)


def _audit_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "app.audit"]


def test_logging_in_is_logged_against_the_account(email, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    _sign_up(email)
    _log_in(email)

    lines = _audit_lines(caplog)

    assert any("login_success" in line and email in line for line in lines)


def test_a_failed_login_is_logged_with_why_but_the_client_is_not_told(email, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    _sign_up(email)
    stranger = f"audit-ghost-{uuid.uuid4().hex[:8]}@example.com"

    wrong = _log_in(email, "not-the-password-1")
    unknown = _log_in(stranger, "not-the-password-1")

    assert wrong.json() == unknown.json()
    lines = _audit_lines(caplog)
    assert any("login_failed" in line and email in line and "credentials" in line for line in lines)
    assert any("login_failed" in line and stranger in line and "credentials" in line for line in lines)


def test_logout_and_a_refused_refresh_are_logged(email, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    _sign_up(email)
    tokens = _log_in(email).json()
    client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    lines = _audit_lines(caplog)

    assert any("logout" in line and "refresh" not in line for line in lines)
    assert any("refresh_rejected" in line for line in lines)


def test_no_secret_ever_reaches_the_log(email, caplog):
    password = "Zq8#rW5!kLp2Hy"
    caplog.set_level(logging.INFO)
    _sign_up(email, password=password)
    tokens = _log_in(email, password=password).json()
    _log_in(email, password="Wr0ng!Passw0rd-x")
    client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})

    everything = " ".join(record.getMessage() for record in caplog.records)

    for secret in (password, "Wr0ng!Passw0rd-x", tokens["refresh_token"], tokens["access_token"]):
        assert secret not in everything


def test_a_client_string_cannot_forge_a_log_line(email, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    _log_in(email, headers={"User-Agent": "curl\nauth_event event=login_success user_id=forged"})

    lines = _audit_lines(caplog)

    assert lines
    assert all("\n" not in line for line in lines)


def test_an_over_long_user_agent_is_cut(email, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    _sign_up(email)
    _log_in(email, headers={"User-Agent": "u" * 5000})

    assert all("u" * 300 not in line for line in _audit_lines(caplog))
