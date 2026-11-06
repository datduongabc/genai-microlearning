"""LTI 1.3 launch end to end against a fake LMS that signs with a real RSA key and publishes a
real key set. Every refusal here is a way someone could otherwise be signed in as someone else."""

import time
import uuid
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import settings
from app.db import RequestSessionLocal, SessionLocal
from app.jobs.maintenance import purge_old_records
from app.lti import launch as lti
from app.main import app
from app.models import Course, LtiIdentity, LtiLaunchCode, LtiLoginState, LtiPlatform, User
from scripts.register_lti_platform import register

client = TestClient(app, follow_redirects=False)

ISSUER = "https://lms.example.edu"
CLIENT_ID = "tool-client-1"
DEPLOYMENT = "dep-1"
INSTRUCTOR = "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"
LEARNER = "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"


class FakeLms:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = uuid.uuid4().hex
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        self.jwks = {"keys": [{**jwk, "kid": self.kid, "alg": "RS256", "use": "sig"}]}

    def id_token(self, nonce, *, key=None, kid=None, **overrides):
        now = int(time.time())
        claims = {
            "iss": ISSUER, "aud": CLIENT_ID, "sub": "lms-user-7", "iat": now, "exp": now + 300, "nonce": nonce,
            "name": "Nguyen Van A", "email": "lecturer@lms.example.edu",
            lti.CLAIM + "version": "1.3.0",
            lti.CLAIM + "message_type": "LtiResourceLinkRequest",
            lti.CLAIM + "deployment_id": DEPLOYMENT,
            lti.CLAIM + "roles": [INSTRUCTOR],
            lti.CLAIM + "context": {"id": "course-42", "title": "Hệ điều hành"},
            lti.CLAIM + "resource_link": {"id": "link-1"},
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": kid or self.kid})


@pytest.fixture
def lms(monkeypatch):
    fake = FakeLms()
    monkeypatch.setattr(settings, "public_url", "https://tool.example.edu")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: fake.jwks)
    lti._jwks_clients.clear()
    with SessionLocal() as db:
        platform = register(db, issuer=ISSUER, client_id=CLIENT_ID, auth_login_url=f"{ISSUER}/auth",
                            jwks_url=f"{ISSUER}/certs", deployments=[DEPLOYMENT], links_moodle=False)
        fake.platform_id = platform.id
    yield fake
    lti._jwks_clients.clear()
    with SessionLocal() as db:
        user_ids = list(db.scalars(select(LtiIdentity.user_id).where(LtiIdentity.platform_id == fake.platform_id)))
        db.query(LtiPlatform).filter(LtiPlatform.id == fake.platform_id).delete()
        db.query(Course).filter(Course.owner_id.in_(user_ids)).delete(synchronize_session=False)
        db.query(LtiLaunchCode).filter(LtiLaunchCode.user_id.in_(user_ids)).delete(synchronize_session=False)
        db.execute(text("DELETE FROM refresh_tokens WHERE user_id = ANY(:ids)"), {"ids": user_ids})
        db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        db.commit()


def start_login(**extra):
    params = {"iss": ISSUER, "login_hint": "hint-1", "client_id": CLIENT_ID,
              "target_link_uri": "https://tool.example.edu/api/lti/launch", **extra}
    response = client.get("/lti/login", params=params)
    assert response.status_code == 302, response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    return {k: v[0] for k, v in query.items()}


def post_launch(id_token, state):
    return client.post("/lti/launch", data={"id_token": id_token, "state": state})


def launch(lms, **claims):
    auth = start_login()
    return post_launch(lms.id_token(auth["nonce"], **claims), auth["state"])


def trade(response):
    fragment = parse_qs(urlparse(response.headers["location"]).fragment)
    return client.post("/lti/session", json={"code": fragment["code"][0]})


def test_an_instructor_launch_signs_in_and_opens_the_course(lms):
    auth = start_login(lti_message_hint="msg-1")
    assert auth["redirect_uri"] == "https://tool.example.edu/api/lti/launch"
    assert (auth["client_id"], auth["login_hint"], auth["lti_message_hint"]) == (CLIENT_ID, "hint-1", "msg-1")
    assert (auth["response_mode"], auth["response_type"], auth["scope"], auth["prompt"]) == (
        "form_post", "id_token", "openid", "none")

    response = post_launch(lms.id_token(auth["nonce"]), auth["state"])

    assert response.status_code == 303, response.text
    location = urlparse(response.headers["location"])
    assert (location.netloc, location.path, location.query) == ("tool.example.edu", "/lti/launch", ""), \
        "the code goes in the fragment, which no server logs"
    session = trade(response)
    assert session.status_code == 200, session.text
    body = session.json()
    assert body["user"]["name"] == "Nguyen Van A"
    assert body["user"]["email"] == "lecturer@lms.example.edu"
    courses = client.get("/courses", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert [(c["id"], c["name"]) for c in courses] == [(body["course_id"], "Hệ điều hành")]
    # The refresh token works like one from a password login.
    assert client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]}).status_code == 200


def test_launching_again_reuses_the_person_and_the_course(lms):
    first = trade(launch(lms)).json()
    second = trade(launch(lms, name="Renamed in LMS")).json()

    assert second["user"]["id"] == first["user"]["id"]
    assert second["course_id"] == first["course_id"]


def test_a_learner_is_refused_and_signed_in_as_no_one(lms):
    response = launch(lms, **{lti.CLAIM + "roles": [LEARNER]})

    assert response.status_code == 400
    assert "giảng viên" in response.text
    with SessionLocal() as db:
        assert db.scalar(select(LtiIdentity).where(LtiIdentity.platform_id == lms.platform_id)) is None


@pytest.mark.parametrize("change", [
    "other_key", "unknown_kid", "wrong_audience", "wrong_issuer", "expired", "wrong_nonce",
    "unknown_deployment", "old_version", "deep_linking", "no_context", "two_audiences_no_azp",
])
def test_a_token_that_does_not_hold_is_refused(lms, change):
    auth = start_login()
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = {
        "other_key": lambda: lms.id_token(auth["nonce"], key=other),
        "unknown_kid": lambda: lms.id_token(auth["nonce"], key=other, kid="not-published"),
        "wrong_audience": lambda: lms.id_token(auth["nonce"], aud="another-tool"),
        "wrong_issuer": lambda: lms.id_token(auth["nonce"], iss="https://evil.example"),
        "expired": lambda: lms.id_token(auth["nonce"], iat=now - 3600, exp=now - 600),
        "wrong_nonce": lambda: lms.id_token("a-nonce-from-another-login"),
        "unknown_deployment": lambda: lms.id_token(auth["nonce"], **{lti.CLAIM + "deployment_id": "dep-9"}),
        "old_version": lambda: lms.id_token(auth["nonce"], **{lti.CLAIM + "version": "1.1"}),
        "deep_linking": lambda: lms.id_token(auth["nonce"], **{lti.CLAIM + "message_type": "LtiDeepLinkingRequest"}),
        "no_context": lambda: lms.id_token(auth["nonce"], **{lti.CLAIM + "context": None}),
        "two_audiences_no_azp": lambda: lms.id_token(auth["nonce"], aud=[CLIENT_ID, "another-tool"]),
    }[change]()

    response = post_launch(token, auth["state"])

    assert response.status_code == 400, response.text
    with SessionLocal() as db:
        assert db.scalar(select(LtiIdentity).where(LtiIdentity.platform_id == lms.platform_id)) is None


def test_a_state_works_once_even_when_its_launch_was_refused(lms):
    auth = start_login()
    assert post_launch(lms.id_token(auth["nonce"], **{lti.CLAIM + "roles": [LEARNER]}), auth["state"]).status_code == 400

    # The same login again with a token that would pass: the state is spent.
    assert post_launch(lms.id_token(auth["nonce"]), auth["state"]).status_code == 400


def test_a_replayed_launch_is_refused(lms):
    auth = start_login()
    token = lms.id_token(auth["nonce"])
    assert post_launch(token, auth["state"]).status_code == 303
    assert post_launch(token, auth["state"]).status_code == 400


def test_a_state_never_issued_is_refused(lms):
    auth = start_login()
    assert post_launch(lms.id_token(auth["nonce"]), "made-up-state").status_code == 400


def test_a_launch_code_works_once(lms):
    response = launch(lms)
    assert trade(response).status_code == 200
    assert trade(response).status_code == 401


def test_an_expired_launch_code_is_refused(lms):
    response = launch(lms)
    with SessionLocal() as db:
        db.execute(text("UPDATE lti_launch_codes SET expires_at = now() - interval '1 second'"))
        db.commit()
    assert trade(response).status_code == 401


def test_the_lms_email_never_joins_an_existing_account(lms):
    with SessionLocal() as db:
        local = User(email="lecturer@lms.example.edu", name="Someone else", hashed_password="x")
        db.add(local)
        db.commit()
        local_id = local.id
    try:
        body = trade(launch(lms)).json()
        assert body["user"]["id"] != str(local_id)
        assert body["user"]["email"].endswith("@lti.invalid")
    finally:
        with SessionLocal() as db:
            db.query(User).filter(User.id == local_id).delete()
            db.commit()


def test_a_course_another_lecturer_opened_is_not_handed_over(lms):
    trade(launch(lms))

    response = launch(lms, sub="lms-user-8", name="Second lecturer", email="second@lms.example.edu")

    assert response.status_code == 400
    assert "giảng viên khác" in response.text


def test_the_project_moodle_confirms_the_moodle_account_and_course(lms):
    with SessionLocal() as db:
        db.get(LtiPlatform, lms.platform_id).links_moodle = True
        linked = User(email=f"moodle-{uuid.uuid4().hex[:6]}@example.com", name="Linked", hashed_password="x",
                      moodle_user_id=7007)
        db.add(linked)
        db.flush()
        existing = Course(name="Linked before", owner_id=linked.id, moodle_course_id=42)
        db.add(existing)
        db.commit()
        linked_id, existing_id = linked.id, existing.id
    try:
        body = trade(launch(lms, sub="7007", **{lti.CLAIM + "context": {"id": "42", "title": "HĐH"}})).json()

        # The account that confirmed Moodle user 7007 through the plugin, and its course for Moodle course 42.
        assert body["user"]["id"] == str(linked_id)
        assert body["course_id"] == str(existing_id)
    finally:
        with SessionLocal() as db:
            db.query(LtiIdentity).filter(LtiIdentity.user_id == linked_id).delete()
            db.query(Course).filter(Course.owner_id == linked_id).delete()
            db.execute(text("DELETE FROM refresh_tokens WHERE user_id = :id"), {"id": linked_id})
            db.query(User).filter(User.id == linked_id).delete()
            db.commit()


def test_a_new_moodle_lecturer_gets_the_moodle_ids(lms):
    with SessionLocal() as db:
        db.get(LtiPlatform, lms.platform_id).links_moodle = True
        db.commit()
    body = trade(launch(lms, sub="7008", **{lti.CLAIM + "context": {"id": "43", "title": "Mạng"}})).json()
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(body["user"]["id"])).moodle_user_id == 7008
        assert db.get(Course, uuid.UUID(body["course_id"])).moodle_course_id == 43


def test_login_is_refused_for_an_unknown_lms_or_another_tool(lms):
    assert client.get("/lti/login", params={"iss": "https://unknown.example", "login_hint": "x"}).status_code == 400
    assert client.get("/lti/login", params={"iss": ISSUER, "login_hint": "x", "client_id": CLIENT_ID,
                                            "target_link_uri": "https://other-tool.example/launch"}).status_code == 400


def test_login_also_accepts_a_form_post(lms):
    response = client.post("/lti/login", data={"iss": ISSUER, "login_hint": "x", "client_id": CLIENT_ID})
    assert response.status_code == 302


def test_the_refusal_page_can_be_shown_inside_the_lms(lms):
    response = launch(lms, **{lti.CLAIM + "roles": [LEARNER]})
    assert "frame-ancestors *" in response.headers["content-security-policy"]
    assert "x-frame-options" not in response.headers
    # Everything else keeps refusing to be framed.
    assert client.get("/health").headers["x-frame-options"] == "DENY"


def test_the_tool_key_set_is_published():
    assert client.get("/lti/jwks").json() == {"keys": []}


def test_expired_logins_and_codes_are_purged(lms):
    start_login()
    launch(lms)
    with SessionLocal() as db:
        db.execute(text("UPDATE lti_login_states SET expires_at = now() - interval '1 second'"))
        db.execute(text("UPDATE lti_launch_codes SET expires_at = now() - interval '1 second'"))
        db.commit()

    assert purge_old_records()["lti"] >= 2

    with SessionLocal() as db:
        assert db.query(LtiLoginState).count() == 0
        assert db.query(LtiLaunchCode).count() == 0


def test_requests_cannot_register_a_platform():
    with RequestSessionLocal() as db, pytest.raises(Exception, match="permission denied"):
        db.add(LtiPlatform(issuer="https://evil.example", client_id="x", auth_login_url="https://e/a",
                           jwks_url="https://e/c"))
        db.flush()
