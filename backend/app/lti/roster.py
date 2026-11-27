"""Course members follow the LMS course, through LTI Names and Role Provisioning Services 2.0.

A student becomes a member of a course here by launching it (app/lti/launch.py). Nothing in a launch
says when they leave the LMS course, so the worker asks the LMS: for each course whose member list is
older than LTI_ROSTER_SYNC_MINUTES, it reads the list from every LMS course that opens it and removes
the members no longer listed as active learners or instructors. Row level security then hides the
course from them on their next request; their login itself is left alone, as they may be a member of
other courses.

To read the list the tool signs an assertion with its own key (LTI_PRIVATE_KEY, published at
/api/lti/jwks) and trades it at the LMS's token endpoint for an access token (OAuth 2 client
credentials, 1EdTech Security Framework 4.1).

Removing someone is the one thing here that cannot be undone by retrying, so nobody is removed unless
the whole picture is known: every LMS course that opens the course must have a member-list address
and a token endpoint, and every list must be read in full. A list that names nobody at all is taken
for a misconfigured LMS, not an empty class. Anything else waits for the next check.
"""

import base64
import functools
import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from sqlalchemy import delete, func, or_, select, text, update

from app.config import settings
from app.db import WorkerSessionLocal, worker_engine
from app.lti.launch import INSTRUCTOR_ROLES, LEARNER_ROLES, same_origin
from app.models import CourseMember, LtiContext, LtiIdentity, LtiPlatform

logger = logging.getLogger(__name__)

NRPS_SCOPE = "https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly"
MEMBERSHIP_MEDIA_TYPE = "application/vnd.ims.lti-nrps.v2.membershipcontainer+json"
ASSERTION_LIFETIME_SECONDS = 300
# How far back the assertion's iat is set. Moodle's JWT library refuses an iat later than its own
# clock with no leeway, so an LMS a second behind refused every token (2026-11-11, a Moodle on a
# laptop against the tool on AWS: "Cannot handle token with iat prior to ..."). Backdating is safe:
# exp still bounds the assertion, and jti makes it single use.
ASSERTION_CLOCK_SKEW_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 10
# A course of a few thousand people is a few dozen pages; a list that goes on longer is a loop.
MAX_PAGES = 200
# Courses looked at per check, so one check cannot hold the worker thread for long.
COURSES_PER_CHECK = 50
# A course whose list could not be read is tried again after this, not on every check: an LMS that
# is down costs a timeout per course, in the thread that also sweeps stuck jobs.
RETRY_AFTER_SKIP = timedelta(minutes=15)
_LOCK_KEY = "lti-roster-sync"
ELIGIBLE_ROLES = LEARNER_ROLES | INSTRUCTOR_ROLES

# Tests swap in an httpx.MockTransport.
_transport: httpx.BaseTransport | None = None


class RosterUnavailable(Exception):
    """The member list could not be read in full; nobody is removed."""


# --- The tool's key


@functools.lru_cache(maxsize=4)
def _load_key(pem: str) -> RSAPrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, RSAPrivateKey):
        raise TypeError("LTI_PRIVATE_KEY must be an RSA key")
    return key


def tool_key() -> RSAPrivateKey | None:
    pem = settings.lti_private_key.strip()
    # An environment variable holds one line, so the PEM's newlines may be written as \n.
    return _load_key(pem.replace("\\n", "\n")) if pem else None


def _b64(number: int) -> str:
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def public_jwks() -> dict:
    """The tool's public key set. The key id is the key's RFC 7638 thumbprint, so a new key gets a
    new id by itself and an LMS that cached the old one fetches the set again."""
    key = tool_key()
    if key is None:
        return {"keys": []}
    numbers = key.public_key().public_numbers()
    jwk = {"e": _b64(numbers.e), "kty": "RSA", "n": _b64(numbers.n)}
    thumbprint = hashlib.sha256(json.dumps(jwk, separators=(",", ":"), sort_keys=True).encode()).digest()
    kid = base64.urlsafe_b64encode(thumbprint).rstrip(b"=").decode()
    return {"keys": [{**jwk, "kid": kid, "alg": "RS256", "use": "sig"}]}


# --- Reading a member list


_tokens: dict[uuid.UUID, tuple[str, float]] = {}
_tokens_lock = threading.Lock()


def _access_token(client: httpx.Client, platform: LtiPlatform) -> str:
    with _tokens_lock:
        cached = _tokens.get(platform.id)
    if cached is not None and cached[1] > time.monotonic():
        return cached[0]
    key = tool_key()
    now = int(time.time())
    assertion = jwt.encode(
        {"iss": platform.client_id, "sub": platform.client_id, "aud": platform.token_url,
         "iat": now - ASSERTION_CLOCK_SKEW_SECONDS, "exp": now + ASSERTION_LIFETIME_SECONDS,
         "jti": uuid.uuid4().hex},
        key, algorithm="RS256", headers={"kid": public_jwks()["keys"][0]["kid"]},
    )
    response = client.post(platform.token_url, data={
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "scope": NRPS_SCOPE,
    })
    if response.status_code != 200:
        # The LMS's error names the problem (invalid_client, invalid_scope) and holds no secret.
        raise RosterUnavailable(f"token endpoint answered {response.status_code}: {response.text[:200]}")
    body = response.json()
    token = body.get("access_token")
    if not token:
        raise RosterUnavailable("token endpoint gave no access_token")
    # A minute early, so a token is never sent just as it runs out.
    lifetime = max(int(body.get("expires_in") or 3600) - 60, 0)
    with _tokens_lock:
        _tokens[platform.id] = (token, time.monotonic() + lifetime)
    return token


def read_roster(client: httpx.Client, platform: LtiPlatform, url: str) -> set[str]:
    """The `sub` of everyone the LMS course lists as an active learner or instructor."""
    token = _access_token(client, platform)
    subs: set[str] = set()
    page_url: str | None = url
    for _ in range(MAX_PAGES):
        response = client.get(page_url, headers={"Authorization": f"Bearer {token}", "Accept": MEMBERSHIP_MEDIA_TYPE})
        if response.status_code == 401:
            with _tokens_lock:
                _tokens.pop(platform.id, None)
        if response.status_code != 200:
            raise RosterUnavailable(f"member list answered {response.status_code}")
        members = response.json().get("members")
        if not isinstance(members, list):
            raise RosterUnavailable("member list without members")
        for member in members:
            if not isinstance(member, dict) or not member.get("user_id"):
                continue
            # Status is optional and means Active when absent; Inactive is a suspended enrolment.
            if member.get("status", "Active") == "Active" and ELIGIBLE_ROLES.intersection(member.get("roles") or []):
                subs.add(str(member["user_id"]))
        page_url = response.links.get("next", {}).get("url")
        if page_url is None:
            return subs
        if not same_origin(page_url, url):
            # The access token goes with every page, so only to the LMS that issued it.
            raise RosterUnavailable(f"next page {page_url!r} is not on the LMS")
    raise RosterUnavailable(f"member list longer than {MAX_PAGES} pages")


# --- Keeping members in step


def sync_rosters(now: datetime | None = None) -> dict[str, int]:
    """One check: the due courses, at most COURSES_PER_CHECK. Returns counts for the log and tests."""
    counts = {"courses": 0, "removed": 0, "skipped": 0}
    if tool_key() is None:
        return counts
    now = now or datetime.now(UTC)
    # Every worker process checks on its own timer. One check at a time, or each process would pick the same
    # due courses and read every list from the LMS once per process. Released with the connection if it dies.
    # Autocommit: a session lock needs no transaction, and one left open for the whole check would sit idle in it.
    with worker_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"), {"key": _LOCK_KEY}):
            return counts
        try:
            _sync_due(now, counts)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": _LOCK_KEY})
    if counts["courses"] or counts["skipped"]:
        logger.info("lti_roster checked=%(courses)d removed=%(removed)d skipped=%(skipped)d", counts)
    return counts


def _sync_due(now: datetime, counts: dict[str, int]) -> None:
    cutoff = now - timedelta(minutes=settings.lti_roster_sync_minutes)
    db = WorkerSessionLocal()
    try:
        due = db.scalars(
            select(LtiContext.course_id)
            .where(LtiContext.memberships_url.is_not(None),
                   or_(LtiContext.roster_synced_at.is_(None), LtiContext.roster_synced_at < cutoff),
                   or_(LtiContext.roster_checked_at.is_(None), LtiContext.roster_checked_at < now - RETRY_AFTER_SKIP))
            .group_by(LtiContext.course_id)
            # The longest untried first, so courses that keep failing cannot take every place.
            .order_by(func.min(LtiContext.roster_checked_at).nulls_first())
            .limit(COURSES_PER_CHECK)
        ).all()
        if not due:
            return
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, transport=_transport) as client:
            for course_id in due:
                try:
                    removed = _sync_course(db, client, course_id, now)
                except (RosterUnavailable, httpx.HTTPError, ValueError) as error:
                    # ValueError: a body that is not JSON. The course is looked at again next check.
                    db.rollback()
                    db.execute(update(LtiContext).where(LtiContext.course_id == course_id)
                               .values(roster_checked_at=now))
                    db.commit()
                    counts["skipped"] += 1
                    logger.warning("lti_roster skipped course=%s: %s", course_id, error)
                    continue
                db.commit()
                counts["courses"] += 1
                counts["removed"] += removed
    finally:
        db.close()


def _sync_course(db, client: httpx.Client, course_id: uuid.UUID, now: datetime) -> int:
    contexts = db.scalars(select(LtiContext).where(LtiContext.course_id == course_id)).all()
    platforms = {p.id: p for p in db.scalars(
        select(LtiPlatform).where(LtiPlatform.id.in_({c.platform_id for c in contexts})))}
    for context in contexts:
        if not context.memberships_url or not platforms[context.platform_id].token_url:
            # A member may have come through this LMS course, and its list cannot be read.
            raise RosterUnavailable(f"context {context.id} has no member list or its LMS no token endpoint")

    active: set[tuple[uuid.UUID, str]] = set()
    for context in contexts:
        platform = platforms[context.platform_id]
        active |= {(platform.id, sub) for sub in read_roster(client, platform, context.memberships_url)}
    if not active:
        raise RosterUnavailable("the LMS lists nobody, not even the instructor")

    members = set(db.scalars(select(CourseMember.user_id).where(CourseMember.course_id == course_id)))
    kept = {user_id for user_id, platform_id, sub in db.execute(
        select(LtiIdentity.user_id, LtiIdentity.platform_id, LtiIdentity.sub)
        .where(LtiIdentity.user_id.in_(members), LtiIdentity.platform_id.in_(platforms)))
        if (platform_id, sub) in active} if members else set()
    gone = members - kept
    if gone:
        db.execute(delete(CourseMember).where(CourseMember.course_id == course_id, CourseMember.user_id.in_(gone)))
        for user_id in sorted(gone, key=str):
            logger.info("lti_roster removed course=%s user=%s", course_id, user_id)
    for context in contexts:
        context.roster_synced_at = context.roster_checked_at = now
    return len(gone)
