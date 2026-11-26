"""Local accounts, made by sign-up, by the admin or by the create-admin command: which passwords
are accepted, what extreme input does, that simultaneous creations with one email make one account
and never a server error, and that the admin logs in with the password."""

import concurrent.futures
import logging
import uuid

import bcrypt
import pytest
from fastapi.testclient import TestClient

from app.auth.accounts import AccountError, create_account
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import RefreshToken, User
from tests.auth_helpers import STRONG_PASSWORD, admin_headers, make_account

client = TestClient(app)
PREFIX = "signup-"


def _delete_user_ids(ids: list) -> None:
    if not ids:
        return
    db = SessionLocal()
    try:
        db.query(RefreshToken).filter(RefreshToken.user_id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def remove_created_users():
    yield
    with SessionLocal() as db:
        ids = [row.id for row in db.query(User.id).filter(User.email.like(f"{PREFIX}%@example.edu"))]
    _delete_user_ids(ids)


@pytest.fixture
def email():
    address = f"acct-{uuid.uuid4().hex[:10]}@example.com"
    yield address
    with SessionLocal() as db:
        ids = [row.id for row in db.query(User.id).filter(User.email == address)]
    _delete_user_ids(ids)


def _email() -> str:
    return f"{PREFIX}{uuid.uuid4().hex[:10]}@example.edu"


def _create(email: str | None = None, password: str = STRONG_PASSWORD, name: str = "Test User"):
    """The result of create_account for an admin: (user, email) or (the AccountError, email)."""
    email = email or _email()
    with SessionLocal() as db:
        try:
            user = create_account(db, email=email, name=name, password=password, is_admin=True)
        except AccountError as error:
            return error, email
        db.refresh(user)
        db.expunge(user)
        return user, email


def _login(email: str, password: str = STRONG_PASSWORD, **kwargs):
    return client.post("/auth/login", json={"email": email, "password": password}, **kwargs)


def test_a_strong_password_creates_an_account_that_can_log_in():
    user, email = _create()

    assert isinstance(user, User) and user.email == email and user.is_admin == True
    login = _login(email, STRONG_PASSWORD)
    assert login.status_code == 200 and login.json()["user"]["is_admin"] is True
    assert "hashed_password" not in login.text and STRONG_PASSWORD not in login.text


def test_the_admin_logs_in_with_the_email_in_any_case():
    _, email = _create()

    assert _login(email.upper(), STRONG_PASSWORD).status_code == 200
    assert _login(f"  {email} ", STRONG_PASSWORD).status_code == 200


def test_non_edu_email_gets_an_account():
    created, _ = _create(email=f"{PREFIX}{uuid.uuid4().hex[:8]}@gmail.com")

    assert isinstance(created, User)


def test_a_short_password_is_refused_and_the_reason_is_given():
    refused, _ = _create(password="Ab1!xyz")

    assert isinstance(refused, AccountError) and refused.status_code == 422
    assert isinstance(refused.detail, str) and refused.detail


def test_a_password_longer_than_72_bytes_is_refused_and_the_reason_is_given():
    # bcrypt only reads the first 72 bytes, so a longer password would be silently cut.
    for password in ("Xk9!vQ2#mTz7" * 6 + "a", "ệ" * 40):
        refused, _ = _create(password=password)

        assert isinstance(refused, AccountError) and refused.status_code == 422, password
        assert "72" in refused.detail


def test_a_password_of_exactly_72_bytes_is_accepted_and_only_that_password_works():
    password = "Xk9!vQ2#mTz7" * 6
    assert len(password.encode()) == 72
    user, email = _create(password=password)

    assert isinstance(user, User)
    assert _login(email, password).status_code == 200
    assert _login(email, password[:-1]).status_code == 401


def test_a_vietnamese_passphrase_within_72_bytes_is_accepted():
    password = "Tôi yêu Việt Nam 2026!"
    _, email = _create(password=password)

    assert _login(email, password).status_code == 200


@pytest.mark.parametrize("password", ["password123", "aaaaaaaaaa", "matkhau123", "nguyenvananh"])
def test_a_long_enough_password_is_accepted_however_simple_it_looks(password):
    # The policy is only length now. The rules that guessed at "weak" passwords were dropped
    # because they push people to passwords like Password1!, which are in every attacker's list,
    # while an ordinary long sentence is harder to guess.
    assert isinstance(_create(password=password)[0], User)


def test_a_blank_name_is_refused_and_a_messy_one_is_cleaned():
    blank, _ = _create(name="   ")
    cleaned, _ = _create(name="  An   Binh  ")

    assert isinstance(blank, AccountError) and blank.status_code == 422
    assert cleaned.name == "An Binh"


def test_an_email_that_is_taken_is_a_conflict_whatever_its_case():
    email = _email()
    assert isinstance(_create(email=email)[0], User)

    for again in (email, email.upper()):
        refused, _ = _create(email=again)
        assert isinstance(refused, AccountError) and refused.status_code == 409


def test_simultaneous_creations_with_one_email_make_one_account_and_no_error_other_than_a_conflict():
    email = _email()

    def create(_):
        user, _ = _create(email=email)
        return 201 if isinstance(user, User) else user.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        codes = sorted(pool.map(create, range(6)))

    assert codes == [201, 409, 409, 409, 409, 409]
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == email).count() == 1


@pytest.mark.parametrize("password", ["a" * 73, "ă" * 100, "x" * 5000, "y" * 200_000], ids=["73", "100-multibyte", "5k", "200k"])
def test_logging_in_with_an_extremely_long_password_is_never_a_server_error(password):
    _, email = _create()

    assert _login(email, password).status_code in (401, 422)


def test_a_wrong_password_and_an_unknown_username_are_indistinguishable():
    _, email = _create()

    wrong = _login(email, "definitely-not-it-1")
    unknown = _login(_email(), "definitely-not-it-1")

    assert (wrong.status_code, wrong.json()) == (unknown.status_code, unknown.json())
    assert wrong.status_code == 401


def test_an_older_account_created_with_a_password_over_72_bytes_can_still_log_in():
    # bcrypt before 5.0 silently used only the first 72 bytes, so such accounts exist.
    password = "ă" * 50
    legacy_hash = bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")
    email = _email()
    with SessionLocal() as db:
        db.add(User(email=email, name="Legacy", hashed_password=legacy_hash, is_admin=True))
        db.commit()

    assert _login(email, password).status_code == 200
    assert _login(email, "ă" * 20).status_code == 401


# --- No account lockout on wrong passwords (merged from test_login_no_lockout.py) ---


def test_many_wrong_passwords_do_not_lock_the_right_one_out(email):
    make_account(email, name="No Lock", is_admin=True)

    wrong = [_login(email, f"Wr0ng!Passw0rd-{i}").status_code for i in range(12)]

    assert wrong == [401] * 12
    assert _login(email, STRONG_PASSWORD).status_code == 200


def test_an_email_that_does_not_exist_is_refused_the_same_way_however_often_it_is_tried():
    ghost = f"nolock-ghost-{uuid.uuid4().hex[:8]}@example.com"

    answers = [_login(ghost, "Wr0ng!Passw0rd-x") for _ in range(12)]

    assert {r.status_code for r in answers} == {401}
    assert len({r.text for r in answers}) == 1


# --- Open registration and local accounts (merged from test_local_accounts.py) ---


def _register_open_account():
    address = f"open-{uuid.uuid4().hex}@gmail.com"
    response = client.post(
        "/auth/register", json={"email": address, "name": "Local User", "password": STRONG_PASSWORD}
    )
    assert response.status_code == 201
    assert response.json()["is_admin"] is False
    return address, response.json()["id"]


def test_any_email_can_register_and_log_in_locally_without_moodle():
    address, user_id = _register_open_account()
    response = _login(address.upper(), STRONG_PASSWORD)
    assert response.status_code == 200
    assert response.json()["user"]["id"] == user_id
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(user_id)).moodle_user_id is None
    assert (
        client.post(
            "/auth/register", json={"email": address, "name": "Duplicate", "password": STRONG_PASSWORD}
        ).status_code
        == 409
    )


def test_admin_creates_local_non_admin_without_link_and_cannot_create_admin():
    address = f"created-{uuid.uuid4().hex}@gmail.com"
    response = client.post(
        "/admin/users",
        headers=admin_headers(),
        json={"email": address, "name": "Created User", "password": STRONG_PASSWORD, "is_admin": True},
    )
    assert response.status_code == 201
    assert response.json()["is_admin"] is False
    assert response.json()["moodle_status"] == "not_linked"
    assert _login(address, STRONG_PASSWORD).status_code == 200


def test_registration_can_be_closed_and_says_whom_to_ask(monkeypatch):
    """A public deployment closes self registration: every account would get the daily generation
    quota, so anyone could create accounts to spend the provider credit. Accounts then come from the
    administrator or from an LMS launch."""
    assert client.get("/auth/options").json() == {"self_registration": True}
    monkeypatch.setattr(settings, "self_registration", False)
    assert client.get("/auth/options").json() == {"self_registration": False}

    address = f"closed-{uuid.uuid4().hex}@gmail.com"
    response = client.post(
        "/auth/register", json={"email": address, "name": "Closed", "password": STRONG_PASSWORD}
    )
    assert response.status_code == 403
    assert "quản trị viên" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == address).count() == 0

    # The administrator can still create accounts.
    created = client.post(
        "/admin/users",
        headers=admin_headers(),
        json={"email": address, "name": "Created", "password": STRONG_PASSWORD},
    )
    assert created.status_code == 201


# --- Authentication audit logging (merged from test_auth_log.py) ---


def _sign_up_audit(address: str, password: str = STRONG_PASSWORD):
    # A local account whose login audit events are tested.
    return make_account(address, password, name="Audit User", is_admin=True)


def _audit_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "app.audit"]


def test_logging_in_is_logged_against_the_account(email, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    _sign_up_audit(email)
    _login(email)

    lines = _audit_lines(caplog)

    assert any("login_success" in line and email in line for line in lines)


def test_a_failed_login_is_logged_with_why_but_the_client_is_not_told(email, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    _sign_up_audit(email)
    stranger = f"audit-ghost-{uuid.uuid4().hex[:8]}@example.com"

    wrong = _login(email, "not-the-password-1")
    unknown = _login(stranger, "not-the-password-1")

    assert wrong.json() == unknown.json()
    lines = _audit_lines(caplog)
    assert any("login_failed" in line and email in line and "credentials" in line for line in lines)
    assert any("login_failed" in line and stranger in line and "credentials" in line for line in lines)


def test_logout_and_a_refused_refresh_are_logged(email, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    _sign_up_audit(email)
    tokens = _login(email).json()
    client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    lines = _audit_lines(caplog)

    assert any("logout" in line and "refresh" not in line for line in lines)
    assert any("refresh_rejected" in line for line in lines)


def test_no_secret_ever_reaches_the_log(email, caplog):
    password = "Zq8#rW5!kLp2Hy"
    caplog.set_level(logging.INFO)
    _sign_up_audit(email, password=password)
    tokens = _login(email, password=password).json()
    _login(email, password="Wr0ng!Passw0rd-x")
    client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})

    everything = " ".join(record.getMessage() for record in caplog.records)

    for secret in (password, "Wr0ng!Passw0rd-x", tokens["refresh_token"], tokens["access_token"]):
        assert secret not in everything


def test_a_client_string_cannot_forge_a_log_line(email, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    _login(email, headers={"User-Agent": "curl\nauth_event event=login_success user_id=forged"})

    lines = _audit_lines(caplog)

    assert lines
    assert all("\n" not in line for line in lines)


def test_an_over_long_user_agent_is_cut(email, caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    _sign_up_audit(email)
    _login(email, headers={"User-Agent": "u" * 5000})

    assert all("u" * 300 not in line for line in _audit_lines(caplog))
