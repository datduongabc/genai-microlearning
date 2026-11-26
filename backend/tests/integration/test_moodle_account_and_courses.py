"""Moodle account linking, unlinking, unconfigured environment behavior, and course linking permissions."""

import re
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import ComponentType, Course, Document, Module, ModuleComponent, User
from app.moodle import client as moodle_client
from app.moodle.errors import MOODLE_NOT_CONFIGURED
from tests.auth_helpers import admin_headers, create_and_login

client = TestClient(app)
REAL_TEACHER_COURSES = moodle_client.teacher_courses


def _link_request(headers: dict):
    return client.post("/moodle/account/link-requests", headers=headers, json={"email": "teacher@moodle.example"})


# --- Unconfigured Moodle environment (merged from test_moodle_not_configured.py) ---


@pytest.fixture
def no_moodle(monkeypatch):
    monkeypatch.setattr(settings, "moodle_base_url", "")
    sent = []
    monkeypatch.setattr(moodle_client.httpx, "post", lambda *a, **kw: sent.append(a) or pytest.fail("no request"))
    return sent


def _not_configured_headers():
    data = create_and_login(client, f"nomoodle-{uuid.uuid4().hex}@example.com")
    return {"Authorization": "Bearer " + data["access_token"]}


def test_the_account_is_unavailable_not_an_error(no_moodle):
    response = client.get("/moodle/account", headers=_not_configured_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert no_moodle == []


def test_a_link_request_says_moodle_is_not_connected(no_moodle):
    response = client.post(
        "/moodle/account/link-requests", headers=_not_configured_headers(), json={"email": "a@moodle.example"}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == MOODLE_NOT_CONFIGURED
    assert no_moodle == []


def test_html_moodle_response_returns_502_from_the_api(monkeypatch):
    login = create_and_login(client, f"moodle-html-{uuid.uuid4().hex}@example.com")
    with SessionLocal() as db:
        db.get(User, uuid.UUID(login["user"]["id"])).moodle_user_id = 10_000 + uuid.uuid4().int % 10**9
        db.commit()
    monkeypatch.setattr(moodle_client, "teacher_courses", REAL_TEACHER_COURSES)
    monkeypatch.setattr(
        moodle_client.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(
            200, text="<html>unavailable</html>", request=httpx.Request("POST", url)
        ),
    )
    response = client.get(
        "/courses/moodle/courses", headers={"Authorization": "Bearer " + login["access_token"]}
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "Thao tác với Moodle thất bại, vui lòng thử lại sau."}


# --- Moodle course permissions (merged from test_moodle_course_permissions.py) ---


@pytest.fixture
def linked(monkeypatch):
    login = create_and_login(client, f"permissions-{uuid.uuid4().hex}@example.com")
    headers = {"Authorization": "Bearer " + login["access_token"]}
    with SessionLocal() as db:
        db.get(User, uuid.UUID(login["user"]["id"])).moodle_user_id = 10_000 + uuid.uuid4().int % 10**9
        db.commit()
    state = {
        "exists": True,
        "email": login["user"]["email"],
        "courses": [{"id": 72, "fullname": "Teacher course", "shortname": "T72"}],
    }
    monkeypatch.setattr(moodle_client, "teacher_courses", lambda user_id: state)

    def forbidden(*args, **kwargs):
        pytest.fail("App must not discover all courses or enroll users")

    for name in ["list_courses", "get_course", "enrol_as_teacher", "create_course"]:
        monkeypatch.setattr(moodle_client, name, forbidden, raising=False)
    return headers, state


def _create_course(headers):
    return client.post("/courses", headers=headers, json={"name": "Owned course"}).json()["id"]


def test_list_and_link_only_current_teacher_courses(linked):
    headers, state = linked
    assert client.get("/courses/moodle/courses", headers=headers).json() == state["courses"]
    cid = _create_course(headers)
    assert client.post(f"/courses/{cid}/link-moodle", headers=headers, json={"moodle_course_id": 73}).status_code == 403
    assert client.post(f"/courses/{cid}/link-moodle", headers=headers, json={"moodle_course_id": 72}).status_code == 200
    state["courses"] = []
    assert client.post(f"/courses/{cid}/link-moodle", headers=headers, json={"moodle_course_id": 72}).status_code == 403
    assert client.get(f"/courses/{cid}/moodle-sections", headers=headers).status_code == 403


def test_more_than_one_app_course_can_link_same_authorized_moodle_course(linked):
    headers, _ = linked
    for _ in range(2):
        cid = _create_course(headers)
        assert (
            client.post(f"/courses/{cid}/link-moodle", headers=headers, json={"moodle_course_id": 72}).status_code == 200
        )
    assert client.post(f"/courses/{cid}/create-moodle", headers=headers).status_code == 404


def test_outage_and_stale_identity_do_not_grant_course_access(linked, monkeypatch):
    headers, state = linked
    cid = _create_course(headers)

    def unavailable(user_id):
        raise moodle_client.MoodleError("Moodle unavailable")

    monkeypatch.setattr(moodle_client, "teacher_courses", unavailable)
    assert client.post(f"/courses/{cid}/link-moodle", headers=headers, json={"moodle_course_id": 72}).status_code == 502
    monkeypatch.setattr(moodle_client, "teacher_courses", lambda user_id: {**state, "suspended": True, "courses": []})
    assert client.post(f"/courses/{cid}/link-moodle", headers=headers, json={"moodle_course_id": 72}).status_code == 403
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"
    monkeypatch.setattr(moodle_client, "teacher_courses", lambda user_id: {**state, "exists": False, "courses": []})
    assert client.post(f"/courses/{cid}/link-moodle", headers=headers, json={"moodle_course_id": 72}).status_code == 422


def test_app_course_ownership_is_still_enforced_and_sections_require_a_link(linked):
    headers, _ = linked
    cid = _create_course(headers)
    stranger = create_and_login(client, f"stranger-{uuid.uuid4().hex}@example.com")
    stranger_headers = {"Authorization": "Bearer " + stranger["access_token"]}
    assert (
        client.post(
            f"/courses/{cid}/link-moodle", headers=stranger_headers, json={"moodle_course_id": 72}
        ).status_code
        == 404
    )
    assert client.get(f"/courses/{cid}/moodle-sections", headers=stranger_headers).status_code == 404
    assert client.get(f"/courses/{cid}/moodle-sections", headers=headers).status_code == 400


def test_unlinked_moodle_account_cannot_link_a_course(linked):
    headers, _ = linked
    cid = _create_course(headers)
    with SessionLocal() as db:
        owner = db.get(Course, uuid.UUID(cid)).owner_id
        db.get(User, owner).moodle_user_id = None
        db.commit()
    assert client.post(f"/courses/{cid}/link-moodle", headers=headers, json={"moodle_course_id": 72}).status_code == 422


# --- Moodle account unlink (merged from test_moodle_account_unlink.py) ---


@pytest.fixture
def unlink_service(monkeypatch):
    state = {"status": "pending", "moodle_user_id": 10_000 + uuid.uuid4().int % 10**9}
    monkeypatch.setattr(moodle_client, "request_account_link", lambda **kw: {"request_id": uuid.uuid4().hex})
    monkeypatch.setattr(moodle_client, "account_link_status", lambda **kw: state.copy())
    monkeypatch.setattr(
        moodle_client,
        "teacher_courses",
        lambda uid: {"exists": True, "email": "teacher@moodle.example", "courses": []},
    )
    return state


def _unlink_account():
    data = create_and_login(client, f"unlink-{uuid.uuid4().hex}@example.com")
    return uuid.UUID(data["user"]["id"]), {"Authorization": "Bearer " + data["access_token"]}


def _assert_cleared(user_id):
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert (user.moodle_user_id, user.moodle_request_id, user.moodle_request_email) == (None, None, None)


@pytest.mark.parametrize("admin_unlink", [False, True])
def test_unlink_clears_all_fields_and_is_idempotent(unlink_service, admin_unlink):
    user_id, headers = _unlink_account()
    assert _link_request(headers).status_code == 202
    with SessionLocal() as db:
        db.get(User, user_id).moodle_user_id = unlink_service["moodle_user_id"]
        db.commit()
    auth = admin_headers() if admin_unlink else headers
    for _ in range(2):
        response = (
            client.post(f"/admin/users/{user_id}/moodle-unlink", headers=auth)
            if admin_unlink
            else client.delete("/moodle/account", headers=auth)
        )
        assert response.status_code == 200
        _assert_cleared(user_id)
    unlink_service["status"] = "confirmed"
    assert client.get("/moodle/account", headers=headers).json()["status"] == "not_linked"


@pytest.mark.parametrize("same_account", [False, True])
def test_identity_can_relink_after_unlink(unlink_service, same_account):
    first_id, first = _unlink_account()
    second_id, second = _unlink_account()
    assert _link_request(first).status_code == 202
    unlink_service["status"] = "confirmed"
    assert client.get("/moodle/account", headers=first).json()["status"] == "linked"
    assert client.delete("/moodle/account", headers=first).status_code == 200
    target = first if same_account else second
    unlink_service["status"] = "pending"
    assert _link_request(target).status_code == 202
    assert client.get("/moodle/account", headers=target).json()["status"] == "pending"
    unlink_service["status"] = "confirmed"
    assert client.get("/moodle/account", headers=target).json()["status"] == "linked"
    with SessionLocal() as db:
        assert db.get(User, first_id if same_account else second_id).moodle_user_id == unlink_service["moodle_user_id"]
    if not same_account:
        _assert_cleared(first_id)


def test_unlink_requires_auth_and_admin_route_refuses_non_admin(unlink_service):
    target_id, target = _unlink_account()
    _, stranger = _unlink_account()
    assert _link_request(target).status_code == 202
    unlink_service["status"] = "confirmed"
    assert client.get("/moodle/account", headers=target).json()["status"] == "linked"
    assert client.delete("/moodle/account").status_code == 401
    assert client.post(f"/admin/users/{target_id}/moodle-unlink", headers=stranger).status_code == 404
    with SessionLocal() as db:
        assert db.get(User, target_id).moodle_user_id == unlink_service["moodle_user_id"]
    assert client.post(f"/admin/users/{uuid.uuid4()}/moodle-unlink", headers=admin_headers()).status_code == 404


@pytest.mark.parametrize("admin_unlink", [False, True])
def test_unlink_discards_pending_consent(unlink_service, admin_unlink):
    user_id, headers = _unlink_account()
    assert _link_request(headers).status_code == 202
    response = (
        client.post(f"/admin/users/{user_id}/moodle-unlink", headers=admin_headers())
        if admin_unlink
        else client.delete("/moodle/account", headers=headers)
    )
    assert response.status_code == 200
    unlink_service["status"] = "confirmed"
    assert client.get("/moodle/account", headers=headers).json()["status"] == "not_linked"
    _assert_cleared(user_id)


@pytest.mark.parametrize("with_content", [False, True])
def test_course_link_survives_unlink_and_publish_explains_missing_account(unlink_service, with_content):
    user_id, headers = _unlink_account()
    assert _link_request(headers).status_code == 202
    unlink_service["status"] = "confirmed"
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"
    with SessionLocal() as db:
        course = Course(name="Preserved Moodle link", owner_id=user_id, moodle_course_id=123)
        db.add(course)
        db.flush()
        document = Document(course_id=course.id, filename="unlink.pdf")
        db.add(document)
        db.flush()
        module = Module(course_id=course.id, document_id=document.id, title="Unlink test")
        db.add(module)
        db.flush()
        if with_content:
            db.add(
                ModuleComponent(module_id=module.id, type=ComponentType.content, content={"markdown": "Content to publish"})
            )
        db.commit()
        course_id, module_id = course.id, module.id
    assert client.delete("/moodle/account", headers=headers).status_code == 200
    with SessionLocal() as db:
        assert db.get(Course, course_id).moodle_course_id == 123
    response = client.post(f"/modules/{module_id}/publish-to-moodle", headers=headers)
    assert response.status_code == 422
    assert "chưa liên kết tài khoản Moodle" in response.json()["detail"]


# --- Moodle confirmed link flow (merged from test_moodle_confirmed_link.py) ---


@pytest.fixture
def confirmed_link_service(monkeypatch):
    state = {
        "status": "pending",
        "moodle_user_id": 10_000 + uuid.uuid4().int % 10**9,
        "email": "teacher@moodle.example",
    }
    monkeypatch.setattr(
        moodle_client,
        "request_account_link",
        lambda **kwargs: {
            "request_id": uuid.uuid4().hex + uuid.uuid4().hex,
            "confirmation_url": "http://moodle.example/local/lms_bridge/confirm.php",
        },
        raising=False,
    )
    monkeypatch.setattr(moodle_client, "account_link_status", lambda **kwargs: state.copy(), raising=False)
    monkeypatch.setattr(
        moodle_client,
        "teacher_courses",
        lambda user_id: {"exists": True, "email": state["email"], "courses": []},
        raising=False,
    )
    return state


def _confirmed_account():
    email = f"confirmation-{uuid.uuid4().hex}@gmail.com"
    data = create_and_login(client, email)
    return data["user"]["id"], {"Authorization": "Bearer " + data["access_token"]}


def _stored_link(user_id):
    with SessionLocal() as db:
        return db.get(User, uuid.UUID(user_id)).moodle_user_id


def test_email_request_is_pending_and_does_not_link_before_moodle_confirmation(confirmed_link_service):
    user_id, headers = _confirmed_account()
    response = _link_request(headers)
    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert _stored_link(user_id) is None
    polled = client.get("/moodle/account", headers=headers)
    assert polled.status_code == 200 and polled.json()["status"] == "pending"
    assert _stored_link(user_id) is None


def test_confirmed_request_links_only_the_requesting_app_account(confirmed_link_service):
    user_id, headers = _confirmed_account()
    _, stranger = _confirmed_account()
    assert _link_request(headers).status_code == 202
    confirmed_link_service["status"] = "confirmed"
    assert client.get("/moodle/account", headers=stranger).json()["status"] == "not_linked"
    response = client.get("/moodle/account", headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "linked"
    assert response.json()["email"] == confirmed_link_service["email"]
    assert _stored_link(user_id) == confirmed_link_service["moodle_user_id"]


@pytest.mark.parametrize("status", ["rejected", "expired"])
def test_rejected_or_expired_request_never_links(confirmed_link_service, status):
    user_id, headers = _confirmed_account()
    assert _link_request(headers).status_code == 202
    confirmed_link_service["status"] = status
    response = client.get("/moodle/account", headers=headers)
    assert response.status_code == 200 and response.json()["status"] == status
    assert _stored_link(user_id) is None


def test_one_moodle_account_cannot_link_two_app_accounts(confirmed_link_service):
    _, first = _confirmed_account()
    second_id, second = _confirmed_account()
    assert _link_request(first).status_code == 202
    assert _link_request(second).status_code == 202
    confirmed_link_service["status"] = "confirmed"
    assert client.get("/moodle/account", headers=first).json()["status"] == "linked"
    conflict = client.get("/moodle/account", headers=second)
    assert conflict.status_code == 200
    assert conflict.json()["status"] == "not_linked"
    assert conflict.json()["error"] == "Tài khoản Moodle đã liên kết với một tài khoản khác."
    assert _stored_link(second_id) is None
    assert "error" not in client.get("/moodle/account", headers=second).json()
    assert _link_request(second).status_code == 202


def test_link_request_requires_authentication_and_a_valid_email(confirmed_link_service):
    assert client.post("/moodle/account/link-requests", json={"email": "teacher@moodle.example"}).status_code == 401
    _, headers = _confirmed_account()
    assert (
        client.post("/moodle/account/link-requests", headers=headers, json={"email": "not an email"}).status_code
        == 422
    )


def test_stale_identity_releases_link_but_a_moodle_outage_retains_it(confirmed_link_service, monkeypatch):
    user_id, headers = _confirmed_account()
    assert _link_request(headers).status_code == 202
    confirmed_link_service["status"] = "confirmed"
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"

    def unavailable(user_id):
        raise moodle_client.MoodleError("Unavailable")

    monkeypatch.setattr(moodle_client, "teacher_courses", unavailable)
    assert client.get("/moodle/account", headers=headers).status_code == 502
    assert _stored_link(user_id) is not None
    monkeypatch.setattr(moodle_client, "teacher_courses", lambda user_id: {"exists": False, "email": "", "courses": []})
    assert client.get("/moodle/account", headers=headers).json()["status"] == "not_linked"
    assert _stored_link(user_id) is None


def test_suspended_moodle_identity_keeps_link(confirmed_link_service, monkeypatch):
    user_id, headers = _confirmed_account()
    assert _link_request(headers).status_code == 202
    confirmed_link_service["status"] = "confirmed"
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"
    monkeypatch.setattr(
        moodle_client,
        "teacher_courses",
        lambda user_id: {
            "exists": True,
            "suspended": True,
            "email": confirmed_link_service["email"],
            "courses": [],
        },
    )
    assert client.get("/moodle/account", headers=headers).json()["status"] == "linked"
    assert _stored_link(user_id) == confirmed_link_service["moodle_user_id"]


def test_matching_and_unmatched_requests_have_the_same_public_response(confirmed_link_service):
    responses = []
    for email in ["teacher@moodle.example", "unknown@moodle.example"]:
        user_id, headers = _confirmed_account()
        response = client.post("/moodle/account/link-requests", headers=headers, json={"email": email})
        assert response.status_code == 202
        body = response.json()
        assert set(body) == {"status", "email", "message", "confirmation_url"}
        assert body["email"] == email
        url = urlparse(body.pop("confirmation_url"))
        assert re.fullmatch(r"[a-f0-9]{64}", parse_qs(url.query)["request"][0])
        body.pop("email")
        responses.append((body, url.scheme, url.netloc, url.path))
        polled = client.get("/moodle/account", headers=headers)
        assert polled.json()["status"] == "pending"
        assert polled.json()["email"] == email
        assert _stored_link(user_id) is None
        confirmed_link_service["status"] = "expired"
        assert client.get("/moodle/account", headers=headers).json()["status"] == "expired"
        assert _stored_link(user_id) is None
        confirmed_link_service["status"] = "pending"
    assert responses[0] == responses[1]
    assert responses[0][0] == {
        "status": "pending",
        "message": "Nếu có tài khoản Moodle phù hợp, hãy đăng nhập Moodle để xác nhận liên kết.",
    }


def test_request_unknown_to_moodle_is_cleared_so_a_new_request_can_be_sent(confirmed_link_service, monkeypatch):
    user_id, headers = _confirmed_account()
    assert _link_request(headers).status_code == 202

    def forgotten(**kwargs):
        raise moodle_client.MoodleError("Invalid request", errorcode="invalidrequest")

    monkeypatch.setattr(moodle_client, "account_link_status", forgotten)
    response = client.get("/moodle/account", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "not_linked"
    assert response.json()["error"]
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(user_id)).moodle_request_id is None
    assert "error" not in client.get("/moodle/account", headers=headers).json()
    assert _link_request(headers).status_code == 202


def test_moodle_failure_while_polling_keeps_the_pending_request(confirmed_link_service, monkeypatch):
    user_id, headers = _confirmed_account()
    assert _link_request(headers).status_code == 202

    def unavailable(**kwargs):
        raise moodle_client.MoodleError("Unavailable")

    monkeypatch.setattr(moodle_client, "account_link_status", unavailable)
    assert client.get("/moodle/account", headers=headers).status_code == 502
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(user_id)).moodle_request_id is not None


@pytest.mark.rate_limits
def test_generic_link_requests_keep_the_rate_limit(confirmed_link_service):
    _, headers = _confirmed_account()
    for _ in range(10):
        assert (
            client.post(
                "/moodle/account/link-requests", headers=headers, json={"email": "unknown@moodle.example"}
            ).status_code
            == 202
        )
    assert (
        client.post(
            "/moodle/account/link-requests", headers=headers, json={"email": "unknown@moodle.example"}
        ).status_code
        == 429
    )
