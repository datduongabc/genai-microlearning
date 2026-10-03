"""Accounts are made by an admin, never by the person: which passwords are accepted, what extreme
input does, that simultaneous creations with one email make one account and never a server error,
and that the public sign-up is gone."""

import concurrent.futures
import uuid

import bcrypt
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import RefreshToken, User, UserRole
from tests.auth_helpers import STRONG_PASSWORD, admin_headers

client = TestClient(app)
PREFIX = "signup-"
ADMIN = {}


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
def admin():
    ADMIN["headers"] = admin_headers()
    yield
    ADMIN.clear()


def _email() -> str:
    return f"{PREFIX}{uuid.uuid4().hex[:10]}@example.edu"


def _register(email: str | None = None, password: str = STRONG_PASSWORD, name: str = "Test User", **extra):
    email = email or _email()
    response = client.post(
        "/admin/users", json={"email": email, "name": name, "password": password, **extra}, headers=ADMIN["headers"]
    )
    return response, email


def _login(email: str, password: str):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_there_is_no_public_sign_up():
    response = client.post(
        "/auth/register", json={"email": _email(), "name": "Test User", "password": STRONG_PASSWORD}
    )

    assert response.status_code in (404, 405)


def test_a_strong_password_creates_an_account_that_can_log_in():
    response, email = _register()

    assert response.status_code == 201
    assert response.json()["email"] == email
    assert "hashed_password" not in response.json() and "password" not in response.json()
    assert _login(email, STRONG_PASSWORD).status_code == 200


def test_a_role_asked_for_is_ignored_an_admin_only_makes_lecturers():
    response, _ = _register(role="admin")

    assert response.status_code == 201
    assert response.json()["role"] == "teacher"


def test_a_password_shorter_than_eight_characters_is_refused():
    response, _ = _register(password="Ab1!xyz")

    assert response.status_code == 422


def test_a_password_longer_than_72_bytes_is_refused_and_the_reason_is_given():
    # bcrypt only reads the first 72 bytes, so a longer password would be silently cut.
    for password in ("Xk9!vQ2#mTz7" * 6 + "a", "ệ" * 40):
        response, _ = _register(password=password)

        assert response.status_code == 422, password
        assert isinstance(response.json()["detail"], str)
        assert "72" in response.json()["detail"]


def test_a_password_of_exactly_72_bytes_is_accepted_and_only_that_password_works():
    password = "Xk9!vQ2#mTz7" * 6
    assert len(password.encode()) == 72
    response, email = _register(password=password)

    assert response.status_code == 201
    assert _login(email, password).status_code == 200
    assert _login(email, password[:-1]).status_code == 401


def test_a_vietnamese_passphrase_within_72_bytes_is_accepted():
    password = "Tôi yêu Việt Nam 2026!"
    response, email = _register(password=password)

    assert response.status_code == 201
    assert _login(email, password).status_code == 200


@pytest.mark.parametrize(
    "password",
    ["password123", "aaaaaaaaaa", "matkhau123", "nguyenvananh"],
)
def test_a_long_enough_password_is_accepted_however_simple_it_looks(password):
    # Chính sách giờ chỉ còn độ dài. Các quy tắc đoán "mật khẩu yếu" đã bị bỏ vì
    # chúng đẩy người dùng tới những mật khẩu kiểu Password1!, vốn nằm trong mọi
    # danh sách của kẻ tấn công, trong khi một câu dài bình thường lại khó đoán hơn.
    response, _ = _register(password=password)

    assert response.status_code == 201


def test_a_blank_or_oversized_name_is_refused_and_a_messy_one_is_cleaned():
    assert _register(name="   ")[0].status_code == 422
    assert _register(name="N" * 101)[0].status_code == 422
    assert _register(name="N" * 100)[0].status_code == 201

    cleaned, _ = _register(name="  An   Binh  ")
    assert cleaned.json()["name"] == "An Binh"


def test_registering_an_existing_email_is_a_conflict_whatever_its_case():
    email = _email()
    assert _register(email=email)[0].status_code == 201

    assert _register(email=email)[0].status_code == 409
    assert _register(email=email.upper())[0].status_code == 409


def test_simultaneous_creations_with_one_email_make_one_account_and_no_server_error():
    email = _email()

    def create(_):
        return TestClient(app).post(
            "/admin/users", json={"email": email, "name": "Race", "password": STRONG_PASSWORD},
            headers=ADMIN["headers"],
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        codes = sorted(pool.map(create, range(6)))

    assert codes == [201, 409, 409, 409, 409, 409]
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.email == email).count() == 1
    finally:
        db.close()


@pytest.mark.parametrize("password", ["a" * 73, "ă" * 100, "x" * 5000, "y" * 200_000], ids=["73", "100-multibyte", "5k", "200k"])
def test_logging_in_with_an_extremely_long_password_is_never_a_server_error(password):
    _, email = _register()

    assert _login(email, password).status_code in (401, 422)


def test_a_wrong_password_and_an_unknown_email_are_indistinguishable():
    _, email = _register()

    wrong = _login(email, "definitely-not-it-1")
    unknown = _login(_email(), "definitely-not-it-1")

    assert (wrong.status_code, wrong.json()) == (unknown.status_code, unknown.json()) == (401, wrong.json())


def test_the_password_policy_applies_to_a_reset_by_the_admin_too():
    created, email = _register()
    user_id = created.json()["id"]

    weak = client.post(f"/admin/users/{user_id}/password", json={"password": "short"}, headers=ADMIN["headers"])
    ok = client.post(
        f"/admin/users/{user_id}/password", json={"password": "A brand new long phrase"}, headers=ADMIN["headers"]
    )

    assert weak.status_code == 422
    assert ok.status_code == 200
    assert _login(email, STRONG_PASSWORD).status_code == 401
    assert _login(email, "A brand new long phrase").status_code == 200


def test_an_older_account_created_with_a_password_over_72_bytes_can_still_log_in():
    # bcrypt before 5.0 silently used only the first 72 bytes, so such accounts exist.
    password = "ă" * 50
    legacy_hash = bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")
    email = _email()
    db = SessionLocal()
    try:
        db.add(User(email=email, name="Legacy", hashed_password=legacy_hash, role=UserRole.teacher))
        db.commit()
    finally:
        db.close()

    assert _login(email, password).status_code == 200
    assert _login(email, "ă" * 20).status_code == 401
