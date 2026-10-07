"""Local accounts, which only the create-admin command makes (lecturers are made from Moodle at their
first login): which passwords are accepted, what extreme input does, that simultaneous creations with
one email make one account and never a server error, and that the admin logs in with the password."""

import concurrent.futures
import uuid

import bcrypt
import pytest
from fastapi.testclient import TestClient

from app.auth.accounts import AccountError, create_account
from app.db import SessionLocal
from app.main import app
from app.models import RefreshToken, User, UserRole
from app.moodle import client as moodle_client
from tests.auth_helpers import STRONG_PASSWORD

client = TestClient(app)
PREFIX = "signup-"


@pytest.fixture(autouse=True)
def remove_created_users():
    yield
    db = SessionLocal()
    try:
        ids = [row.id for row in db.query(User.id).filter(User.email.like(f"{PREFIX}%@example.edu"))]
        db.query(RefreshToken).filter(RefreshToken.user_id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def moodle_knows_nobody(monkeypatch):
    """A username that is not an admin goes to Moodle, which here has no such user."""
    monkeypatch.setattr(moodle_client, "verify_login", lambda username, password: None)


def _email() -> str:
    return f"{PREFIX}{uuid.uuid4().hex[:10]}@example.edu"


def _create(email: str | None = None, password: str = STRONG_PASSWORD, name: str = "Test User"):
    """The result of create_account for an admin: (user, email) or (the AccountError, email)."""
    email = email or _email()
    with SessionLocal() as db:
        try:
            user = create_account(db, email=email, name=name, password=password, role=UserRole.admin)
        except AccountError as error:
            return error, email
        db.refresh(user)
        db.expunge(user)
        return user, email


def _login(email: str, password: str):
    return client.post("/auth/login", json={"username": email, "password": password})


def test_a_strong_password_creates_an_account_that_can_log_in():
    user, email = _create()

    assert isinstance(user, User) and user.email == email and user.role == UserRole.admin
    login = _login(email, STRONG_PASSWORD)
    assert login.status_code == 200 and login.json()["user"]["role"] == "admin"
    assert "hashed_password" not in login.text and STRONG_PASSWORD not in login.text


def test_the_admin_logs_in_with_the_email_in_any_case():
    _, email = _create()

    assert _login(email.upper(), STRONG_PASSWORD).status_code == 200
    assert _login(f"  {email} ", STRONG_PASSWORD).status_code == 200


def test_only_an_edu_email_gets_an_account():
    refused, _ = _create(email=f"{PREFIX}{uuid.uuid4().hex[:8]}@gmail.com")

    assert isinstance(refused, AccountError) and refused.status_code == 422 and "edu" in refused.detail


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
        db.add(User(email=email, name="Legacy", hashed_password=legacy_hash, role=UserRole.admin))
        db.commit()

    assert _login(email, password).status_code == 200
    assert _login(email, "ă" * 20).status_code == 401
