"""Course members follow the LMS course (Names and Role Provisioning). Students come in through real
launches against the fake LMS of test_lti_launch; the LMS's token endpoint and member list are an
httpx.MockTransport that checks the tool's signed assertion against the tool's published key set.

Removing someone cannot be retried away, so most of these are about when nobody must be removed."""

import base64
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select, text

from app.config import settings
from app.db import SessionLocal
from app.lti import launch as lti
from app.lti import roster
from app.models import LtiContext, LtiPlatform
from tests.integration.test_lti_launch import (  # noqa: F401 -- lms is a fixture
    ISSUER,
    STUDENT,
    _bearer,
    client,
    launch,
    lms,
    trade,
)

TOKEN_URL = f"{ISSUER}/token"
MEMBERS_URL = f"{ISSUER}/nrps/course-42"
NRPS = {lti.NRPS_CLAIM: {"context_memberships_url": MEMBERS_URL, "service_versions": ["2.0"]}}
INSTRUCTOR = "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"
LEARNER = "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"
OTHER = {**STUDENT, "sub": "lms-student-4", "name": "Le Van C", "email": "c@lms.example.edu"}


def member(sub, role=LEARNER, **extra):
    return {"user_id": sub, "roles": [role], **extra}


TEACHER_ROW = member("lms-user-7", INSTRUCTOR)


class FakeNrps:
    """The LMS's side: a token endpoint and a member list, in pages of `pages`."""

    def __init__(self):
        self.pages = [[TEACHER_ROW, member(STUDENT["sub"]), member(OTHER["sub"])]]
        self.list_status = 200
        self.next_url = None  # a "next" link to send after the last page, to test a foreign one
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if str(request.url) == TOKEN_URL:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            assert form["grant_type"] == "client_credentials"
            assert form["scope"] == roster.NRPS_SCOPE
            # Checked the way the LMS checks it: against the key set the tool publishes.
            key = jwt.PyJWKSet.from_dict(client.get("/lti/jwks").json())[jwt.get_unverified_header(
                form["client_assertion"])["kid"]].key
            claims = jwt.decode(form["client_assertion"], key, algorithms=["RS256"], audience=TOKEN_URL)
            assert claims["iss"] == claims["sub"] == "tool-client-1"
            return httpx.Response(200, json={"access_token": "lms-token", "token_type": "Bearer", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer lms-token"
        assert request.headers["accept"] == roster.MEMBERSHIP_MEDIA_TYPE
        if self.list_status != 200:
            return httpx.Response(self.list_status)
        page = int(request.url.params.get("page", "0"))
        headers = {}
        if page + 1 < len(self.pages):
            headers["link"] = f'<{MEMBERS_URL}?page={page + 1}>; rel="next"'
        elif self.next_url:
            headers["link"] = f'<{self.next_url}>; rel="next"'
        return httpx.Response(200, json={"id": MEMBERS_URL, "members": self.pages[page]}, headers=headers)


@pytest.fixture
def nrps(lms, monkeypatch):  # noqa: F811 -- the fixture of test_lti_launch
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    # Written on one line, as an environment variable holds it.
    monkeypatch.setattr(settings, "lti_private_key", pem.replace("\n", "\\n"))
    fake = FakeNrps()
    fake.lms = lms
    monkeypatch.setattr(roster, "_transport", httpx.MockTransport(fake.handler))
    roster._tokens.clear()
    with SessionLocal() as db:
        db.get(LtiPlatform, lms.platform_id).token_url = TOKEN_URL
        db.commit()
    yield fake
    roster._tokens.clear()


def _class(fake_lms):
    """A lecturer and two students launched with the service on; returns (lecturer, student, other)."""
    lecturer = trade(launch(fake_lms, **NRPS)).json()
    return lecturer, trade(launch(fake_lms, **STUDENT, **NRPS)).json(), trade(launch(fake_lms, **OTHER, **NRPS)).json()


def _members(course_id):
    with SessionLocal() as db:
        return set(db.scalars(text("SELECT user_id::text FROM course_members WHERE course_id = :c"), {"c": course_id}))


def _sync(now=None):
    # Far enough ahead that every list made by this test is due, whatever ran before.
    return roster.sync_rosters(now or datetime.now(UTC) + timedelta(days=1))


def test_a_student_the_lms_no_longer_lists_loses_the_course_and_the_others_keep_it(nrps):
    lecturer, student, other = _class(nrps.lms)
    nrps.pages = [[TEACHER_ROW, member(OTHER["sub"])]]

    counts = _sync()

    assert counts["removed"] == 1
    assert _members(lecturer["course_id"]) == {other["user"]["id"]}
    course = lecturer["course_id"]
    assert client.get(f"/courses/{course}/chat/latest", headers=_bearer(student)).status_code == 404
    assert client.get(f"/courses/{course}/chat/latest", headers=_bearer(other)).status_code == 200
    # Their login is not ended: they may be a member of other courses.
    assert client.post("/auth/refresh", json={"refresh_token": student["refresh_token"]}).status_code == 200
    # Launching again from the LMS, once enrolled again, brings them back.
    nrps.pages = [[TEACHER_ROW, member(STUDENT["sub"]), member(OTHER["sub"])]]
    back = trade(launch(nrps.lms, **STUDENT, **NRPS)).json()
    assert client.get(f"/courses/{course}/chat/latest", headers=_bearer(back)).status_code == 200


def test_a_suspended_enrolment_or_a_role_the_tool_does_not_serve_counts_as_gone(nrps):
    lecturer, _student, _other = _class(nrps.lms)
    nrps.pages = [[TEACHER_ROW, member(STUDENT["sub"], status="Inactive"),
                   member(OTHER["sub"], "http://purl.imsglobal.org/vocab/lis/v2/membership#Mentor")]]

    _sync()

    assert _members(lecturer["course_id"]) == set()


def test_every_page_of_the_list_is_read(nrps):
    lecturer, student, other = _class(nrps.lms)
    nrps.pages = [[TEACHER_ROW], [member(STUDENT["sub"])], [member(OTHER["sub"])]]

    assert _sync()["removed"] == 0
    assert _members(lecturer["course_id"]) == {student["user"]["id"], other["user"]["id"]}


def test_a_list_is_read_once_per_interval(nrps):
    _class(nrps.lms)
    now = datetime.now(UTC) + timedelta(days=1)
    _sync(now)
    calls = len(nrps.calls)

    assert _sync(now + timedelta(minutes=settings.lti_roster_sync_minutes - 1))["courses"] == 0
    assert len(nrps.calls) == calls
    assert _sync(now + timedelta(minutes=settings.lti_roster_sync_minutes + 1))["courses"] >= 1


@pytest.mark.parametrize("fault", ["list_fails", "token_refused", "list_names_nobody", "foreign_next_page"])
def test_nobody_is_removed_when_the_list_cannot_be_trusted(nrps, fault, monkeypatch):
    lecturer, student, other = _class(nrps.lms)
    if fault == "list_fails":
        nrps.list_status = 503
    elif fault == "token_refused":
        monkeypatch.setattr(roster, "_transport", httpx.MockTransport(
            lambda request: httpx.Response(400, json={"error": "invalid_client"})))
    elif fault == "list_names_nobody":
        nrps.pages = [[]]
    else:
        nrps.pages = [[TEACHER_ROW]]
        nrps.next_url = "https://elsewhere.example.com/members?page=2"

    counts = _sync()

    assert counts["removed"] == 0 and counts["skipped"] >= 1
    assert _members(lecturer["course_id"]) == {student["user"]["id"], other["user"]["id"]}
    with SessionLocal() as db:
        context = db.scalar(select(LtiContext).where(LtiContext.course_id == lecturer["course_id"]))
        assert context.roster_synced_at is None, "still due"
        assert context.roster_checked_at is not None, "but behind the others"


def test_a_list_that_could_not_be_read_is_tried_again_only_after_a_while(nrps):
    lecturer, _student, other = _class(nrps.lms)
    nrps.list_status = 503
    now = datetime.now(UTC) + timedelta(days=1)
    assert _sync(now)["skipped"] >= 1
    nrps.list_status = 200
    nrps.pages = [[TEACHER_ROW, member(OTHER["sub"])]]
    calls = len(nrps.calls)

    soon = _sync(now + roster.RETRY_AFTER_SKIP - timedelta(minutes=1))
    assert soon["courses"] == soon["skipped"] == 0 and len(nrps.calls) == calls
    assert _sync(now + roster.RETRY_AFTER_SKIP + timedelta(minutes=1))["removed"] == 1
    assert _members(lecturer["course_id"]) == {other["user"]["id"]}


def test_nobody_is_removed_while_another_lms_course_opening_the_same_course_has_no_list(nrps):
    lecturer, _student, _other = _class(nrps.lms)
    with SessionLocal() as db:
        first = db.scalar(select(LtiContext).where(LtiContext.course_id == lecturer["course_id"]))
        db.add(LtiContext(platform_id=first.platform_id, deployment_id=first.deployment_id, context_id="course-43",
                          course_id=first.course_id))
        db.commit()
    nrps.pages = [[TEACHER_ROW]]

    assert _sync()["removed"] == 0
    assert len(_members(lecturer["course_id"])) == 2


def test_without_a_tool_key_nothing_is_asked_and_no_key_is_published(nrps, monkeypatch):
    _class(nrps.lms)
    monkeypatch.setattr(settings, "lti_private_key", "")
    nrps.calls.clear()

    assert _sync() == {"courses": 0, "removed": 0, "skipped": 0}
    assert nrps.calls == []
    assert client.get("/lti/jwks").json() == {"keys": []}


def test_the_published_key_verifies_what_the_tool_signs_and_its_id_is_its_thumbprint(nrps):
    keys = client.get("/lti/jwks").json()["keys"]

    assert len(keys) == 1 and keys[0]["alg"] == "RS256" and "d" not in keys[0]
    public = jwt.PyJWK.from_dict(keys[0]).key
    token = jwt.encode({"iat": int(time.time())}, roster.tool_key(), algorithm="RS256")
    jwt.decode(token, public, algorithms=["RS256"])
    canonical = json.dumps({k: keys[0][k] for k in ("e", "kty", "n")}, separators=(",", ":"), sort_keys=True)
    assert keys[0]["kid"] == base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest()).rstrip(b"=").decode()


def test_a_launch_keeps_the_latest_member_list_address_and_ignores_one_off_the_lms(nrps):
    lecturer = trade(launch(nrps.lms)).json()
    with SessionLocal() as db:
        assert db.scalar(select(LtiContext.memberships_url).where(LtiContext.course_id == lecturer["course_id"])) is None

    trade(launch(nrps.lms, **STUDENT, **NRPS))
    trade(launch(nrps.lms, **{lti.NRPS_CLAIM: {"context_memberships_url": "https://evil.example.com/members"}}))

    with SessionLocal() as db:
        url = db.scalar(select(LtiContext.memberships_url).where(LtiContext.course_id == lecturer["course_id"]))
    assert url == MEMBERS_URL
