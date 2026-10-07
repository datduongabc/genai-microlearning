"""A lecturer signs themselves up (POST /auth/register) with the same rules as when an admin makes
the account: an edu email, the password policy, one account per email, and always a lecturer. The
account works at once, with no approval, and every attempt is audited and rate limited."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import RefreshToken, User, UserRole
from tests.auth_helpers import STRONG_PASSWORD, admin_headers

client = TestClient(app)
PREFIX = "selfsignup-"


@pytest.fixture(autouse=True)
def remove_created_users():
    yield
    db = SessionLocal()
    try:
        ids = [row.id for row in db.query(User.id).filter(User.email.like(f"{PREFIX}%"))]
        db.query(RefreshToken).filter(RefreshToken.user_id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _email(domain="example.edu") -> str:
    return f"{PREFIX}{uuid.uuid4().hex[:10]}@{domain}"


def _sign_up(email=None, password=STRONG_PASSWORD, name="Nguyễn Thị Lan", **extra):
    email = _email() if email is None else email
    return client.post("/auth/register", json={"email": email, "name": name, "password": password, **extra}), email


def test_a_new_lecturer_can_log_in_straight_away_with_no_approval():
    response, email = _sign_up()

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == email and body["name"] == "Nguyễn Thị Lan" and body["role"] == "teacher"
    assert "password" not in body and "hashed_password" not in body
    login = client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/courses", headers=headers).status_code == 200
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == email).one().is_active is True


@pytest.mark.parametrize("role", ["admin", "teacher", "root"])
def test_a_role_asked_for_is_ignored_sign_up_only_makes_lecturers(role):
    response, _ = _sign_up(role=role, is_active=False)

    assert response.status_code == 201
    assert response.json()["role"] == UserRole.teacher.value


@pytest.mark.parametrize("domain", ["gmail.com", "example.com", "edu.com", "x.edu.com", "school.edu.vn.evil.com"])
def test_only_edu_emails_can_sign_up(domain):
    email = _email(domain)

    response, _ = _sign_up(email)

    assert response.status_code == 422
    assert "edu" in response.json()["detail"]
    assert client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD}).status_code == 401


@pytest.mark.parametrize("domain", ["hcmut.edu.vn", "x.edu", "Mail.HCMUT.EDU.VN"])
def test_edu_emails_are_accepted_whatever_their_case(domain):
    assert _sign_up(_email(domain))[0].status_code == 201


@pytest.mark.parametrize("email", ["not-an-email", "", "a@", "@x.edu"])
def test_a_malformed_email_is_refused(email):
    assert _sign_up(email)[0].status_code == 422


def test_the_password_policy_of_the_admin_form_applies():
    too_short, _ = _sign_up(password="Ab1!xyz")
    too_long, _ = _sign_up(password="Aa1!" + "x" * 80)  # more than the 72 bytes the hash reads
    name_in_it, _ = _sign_up(name="Lan Nguyen", password="lan nguyen 2026")

    assert too_short.status_code == 422 and too_long.status_code == 422
    # Whatever the policy answers an admin, it answers the person signing up.
    admin = client.post(
        "/admin/users",
        json={"email": _email(), "name": "Lan Nguyen", "password": "lan nguyen 2026"},
        headers=admin_headers(),
    )
    assert name_in_it.status_code == admin.status_code
    if admin.status_code != 201:
        assert name_in_it.json()["detail"] == admin.json()["detail"]


def test_an_email_that_is_taken_is_a_conflict_whatever_its_case():
    first, email = _sign_up()

    assert first.status_code == 201
    assert _sign_up(email)[0].status_code == 409
    assert _sign_up(email.upper().replace("EXAMPLE.EDU", "example.edu"))[0].status_code == 409
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == email).count() == 1


def test_an_email_an_admin_already_made_cannot_be_signed_up_again():
    email = _email()
    made = client.post(
        "/admin/users", json={"email": email, "name": "Lan", "password": STRONG_PASSWORD}, headers=admin_headers()
    )
    assert made.status_code == 201

    assert _sign_up(email)[0].status_code == 409


def test_an_empty_or_huge_body_is_refused_without_an_error_page():
    assert client.post("/auth/register", json={}).status_code == 422
    assert _sign_up(name="   ")[0].status_code == 422
    assert _sign_up(name="N" * 101)[0].status_code == 422
    assert _sign_up(password="x" * 1025)[0].status_code == 422
    assert _sign_up(_email() + "x" * 300)[0].status_code == 422


def test_sign_up_and_every_refusal_are_logged_without_the_password(caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    password = "Zq9!unique-sentinel-pw"
    ok, email = _sign_up(password=password)
    _sign_up(email, password=password)  # taken
    _sign_up(_email(), password="short")  # weak
    _sign_up(_email("gmail.com"), password=password)  # not edu

    lines = [r.getMessage() for r in caplog.records if r.name == "app.audit"]

    assert ok.status_code == 201
    assert any("event=register " in line and email in line for line in lines)
    for reason in ("email_taken", "weak_password", "not_edu_email"):
        assert any("register_rejected" in line and reason in line for line in lines), reason
    assert not any(password in line for line in lines)


@pytest.mark.rate_limits
def test_sign_ups_are_rate_limited():
    codes = [_sign_up(password="ngan")[0].status_code for _ in range(11)]

    assert codes[:10] == [422] * 10
    assert codes[10] == 429
