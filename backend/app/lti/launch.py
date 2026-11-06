"""LTI 1.3 resource link launch, the OIDC third-party initiated login of the 1EdTech Security
Framework:

1. The LMS calls login() with the issuer and a login hint. The tool keeps a state and a nonce
   and sends the browser to the LMS's authorization endpoint.
2. The LMS posts an id_token, a JWT it signed, and the state to launch(). The tool spends the
   state, checks the signature against the LMS's published keys and the claims against the
   registration, and maps the person and the course.
3. The browser is sent to the frontend with a launch code, which it trades once for the same
   tokens a password login gives (exchange()).

No cookie is used at any step: the tool often runs in an iframe of the LMS, where browsers
block third-party cookies. The state lives in the database, so any api task can finish a
launch another one started.

Only instructors are let in. A student launch is refused until course membership exists in
row level security (docs/architecture/scalable-design.md, section 14).
"""

import hashlib
import logging
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import jwt
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.accounts import AccountError, clean_name, normalize_email
from app.auth.security import hash_password
from app.config import settings
from app.models import (
    Course,
    LtiContext,
    LtiDeployment,
    LtiIdentity,
    LtiLaunchCode,
    LtiLoginState,
    LtiPlatform,
    User,
)

logger = logging.getLogger(__name__)

LOGIN_STATE_TTL = timedelta(minutes=10)
LAUNCH_CODE_TTL = timedelta(seconds=60)
CLOCK_SKEW_SECONDS = 60

CLAIM = "https://purl.imsglobal.org/spec/lti/claim/"
INSTRUCTOR_ROLES = {
    "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor",
    # The short form LTI 1.1 used, which some platforms still send.
    "Instructor",
}


class LaunchRefused(Exception):
    """A launch that must not sign anyone in. `message` is shown to the person; `reason` is
    for the log, which says what failed without echoing the token."""

    def __init__(self, reason: str, message: str = "Không mở được công cụ từ LMS. Hãy thử mở lại hoạt động."):
        super().__init__(reason)
        self.reason = reason
        self.message = message


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def redirect_uri() -> str:
    """Must equal, character for character, a redirect URI registered in the LMS."""
    return f"{settings.public_url.rstrip('/')}/api/lti/launch"


# --- 1. Login initiation


def find_platform(db: Session, issuer: str, client_id: str | None) -> LtiPlatform:
    query = select(LtiPlatform).where(LtiPlatform.issuer == issuer)
    if client_id:
        query = query.where(LtiPlatform.client_id == client_id)
    platforms = db.scalars(query).all()
    # client_id is optional in a login initiation; without it the issuer must be unambiguous.
    if len(platforms) != 1:
        raise LaunchRefused(f"platform issuer={issuer!r} client_id={client_id!r} matched {len(platforms)}")
    return platforms[0]


def login(db: Session, *, issuer: str, login_hint: str, target_link_uri: str | None,
          client_id: str | None, lti_message_hint: str | None, now: datetime | None = None) -> str:
    """Where to send the browser next: the LMS's authorization endpoint. The caller commits."""
    platform = find_platform(db, issuer, client_id)
    if target_link_uri and not target_link_uri.startswith(settings.public_url.rstrip("/")):
        # A launch the LMS means for another tool must not be completed here.
        raise LaunchRefused(f"target_link_uri {target_link_uri!r} is not this tool")
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = now or datetime.now(UTC)
    db.add(LtiLoginState(state_hash=_hash(state), nonce=nonce, platform_id=platform.id,
                         expires_at=now + LOGIN_STATE_TTL))
    params = {
        "scope": "openid",
        "response_type": "id_token",
        "response_mode": "form_post",
        "prompt": "none",
        "client_id": platform.client_id,
        "redirect_uri": redirect_uri(),
        "login_hint": login_hint,
        "state": state,
        "nonce": nonce,
    }
    if lti_message_hint:
        params["lti_message_hint"] = lti_message_hint
    separator = "&" if "?" in platform.auth_login_url else "?"
    return f"{platform.auth_login_url}{separator}{urlencode(params)}"


# --- 2. Launch

_jwks_clients: dict[str, jwt.PyJWKClient] = {}
_jwks_lock = threading.Lock()


def _signing_key(jwks_url: str, token: str):
    """The LMS's key for this token. The key set is cached per URL, and fetched again for a key
    id it does not have, which is how a rotated key is picked up."""
    with _jwks_lock:
        client = _jwks_clients.get(jwks_url)
        if client is None:
            client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=5)
            _jwks_clients[jwks_url] = client
    try:
        return client.get_signing_key_from_jwt(token).key
    except jwt.PyJWKClientError as error:
        raise LaunchRefused(f"signing key: {error}") from error


def _spend_state(db: Session, state: str, now: datetime) -> LtiLoginState:
    # DELETE ... RETURNING: two posts of one state cannot both get it.
    spent = db.execute(
        delete(LtiLoginState).where(LtiLoginState.state_hash == _hash(state)).returning(LtiLoginState)
    ).scalar_one_or_none()
    if spent is None:
        raise LaunchRefused("unknown or used state")
    if spent.expires_at <= now:
        raise LaunchRefused("expired state", "Phiên mở công cụ đã hết hạn. Hãy mở lại hoạt động từ LMS.")
    return spent


def discard_state(db: Session, state: str) -> None:
    db.execute(delete(LtiLoginState).where(LtiLoginState.state_hash == _hash(state)))


@dataclass
class LaunchClaims:
    sub: str
    deployment_id: str
    context_id: str
    context_title: str
    name: str
    email: str | None
    roles: list[str]


def validate_id_token(db: Session, platform: LtiPlatform, id_token: str, nonce: str) -> LaunchClaims:
    try:
        claims = jwt.decode(
            id_token,
            _signing_key(platform.jwks_url, id_token),
            algorithms=["RS256"],
            audience=platform.client_id,
            issuer=platform.issuer,
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "sub", "nonce"]},
        )
    except jwt.InvalidTokenError as error:
        raise LaunchRefused(f"id_token: {type(error).__name__}: {error}") from error

    # With several audiences, the token must say it was issued for this tool (OIDC Core 3.1.3.7).
    audience = claims.get("aud")
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != platform.client_id:
        raise LaunchRefused("azp is not this tool")
    if not secrets.compare_digest(str(claims["nonce"]), nonce):
        raise LaunchRefused("nonce does not match the login")
    if claims.get(CLAIM + "version") != "1.3.0":
        raise LaunchRefused(f"version {claims.get(CLAIM + 'version')!r}")
    if claims.get(CLAIM + "message_type") != "LtiResourceLinkRequest":
        raise LaunchRefused(f"message_type {claims.get(CLAIM + 'message_type')!r}",
                            "Công cụ chỉ mở được từ một hoạt động trong khóa học.")

    deployment_id = claims.get(CLAIM + "deployment_id")
    known = db.scalar(select(LtiDeployment.id).where(
        LtiDeployment.platform_id == platform.id, LtiDeployment.deployment_id == str(deployment_id)))
    if known is None:
        raise LaunchRefused(f"deployment {deployment_id!r} not registered")

    context = claims.get(CLAIM + "context") or {}
    if not context.get("id"):
        raise LaunchRefused("no context", "Hãy mở công cụ từ bên trong một khóa học.")

    name = claims.get("name") or " ".join(p for p in (claims.get("given_name"), claims.get("family_name")) if p)
    return LaunchClaims(
        sub=str(claims["sub"]),
        deployment_id=str(deployment_id),
        context_id=str(context["id"]),
        context_title=str(context.get("title") or context.get("label") or "Khóa học từ LMS"),
        name=name or "Giảng viên",
        email=claims.get("email"),
        roles=list(claims.get(CLAIM + "roles") or []),
    )


def _act_as(db: Session, user_id: uuid.UUID) -> None:
    """From here the request is this user's, under row level security like any signed-in request
    (see app/api/deps.py: SET LOCAL now, and again on every later transaction)."""
    db.info["rls_user_id"] = user_id
    db.execute(text(f"SET LOCAL app.current_user_id = '{uuid.UUID(str(user_id))}'"))


def _moodle_id(value: str) -> int | None:
    return int(value) if value.isdigit() else None


def _user_for(db: Session, platform: LtiPlatform, claims: LaunchClaims) -> User:
    identity = db.scalar(select(LtiIdentity).where(
        LtiIdentity.platform_id == platform.id, LtiIdentity.sub == claims.sub))
    if identity is not None:
        user = db.get(User, identity.user_id)
    else:
        user = None
        moodle_user_id = _moodle_id(claims.sub) if platform.links_moodle else None
        if moodle_user_id is not None:
            # The account that already confirmed this Moodle identity through the plugin is the
            # same person, now confirmed by Moodle's signature as well.
            user = db.scalar(select(User).where(User.moodle_user_id == moodle_user_id))
        if user is None:
            user = _new_user(db, claims, moodle_user_id)
        db.add(LtiIdentity(platform_id=platform.id, sub=claims.sub, user_id=user.id))
    if user is None or not user.is_active:
        raise LaunchRefused("account disabled", "Tài khoản đã bị vô hiệu hóa. Vui lòng liên hệ admin.")
    return user


def _new_user(db: Session, claims: LaunchClaims, moodle_user_id: int | None) -> User:
    # The LMS's email is used when it is free. It is never used to join an existing account:
    # nothing in a launch proves the person owns that address here.
    email = None
    if claims.email:
        try:
            email = normalize_email(claims.email)
        except AccountError:
            email = None
        if email and db.scalar(select(User.id).where(User.email == email)) is not None:
            email = None
    try:
        name = clean_name(claims.name)
    except AccountError:
        name = "Giảng viên"
    user = User(
        email=email or f"lti-{uuid.uuid4().hex}@lti.invalid",
        name=name[:100],
        # A password nobody knows: this account signs in through the LMS only.
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        moodle_user_id=moodle_user_id,
    )
    db.add(user)
    db.flush()
    return user


def _course_for(db: Session, platform: LtiPlatform, claims: LaunchClaims, user: User) -> Course:
    context = db.scalar(select(LtiContext).where(
        LtiContext.platform_id == platform.id, LtiContext.deployment_id == claims.deployment_id,
        LtiContext.context_id == claims.context_id))
    if context is not None:
        # Under row level security: another lecturer's course is not visible, so None.
        course = db.get(Course, context.course_id)
        if course is None:
            raise LaunchRefused(
                "context owned by another user",
                "Khóa học này đã được một giảng viên khác mở trong công cụ. "
                "Hiện mỗi khóa học thuộc về một giảng viên.")
        return course

    moodle_course_id = _moodle_id(claims.context_id) if platform.links_moodle else None
    course = None
    if moodle_course_id is not None:
        # The lecturer may already have linked a course here to this Moodle course.
        course = db.scalar(select(Course).where(
            Course.owner_id == user.id, Course.moodle_course_id == moodle_course_id).order_by(Course.created_at))
    if course is None:
        course = Course(name=claims.context_title[:200], owner_id=user.id, moodle_course_id=moodle_course_id)
        db.add(course)
        db.flush()
    db.add(LtiContext(platform_id=platform.id, deployment_id=claims.deployment_id,
                      context_id=claims.context_id, course_id=course.id))
    return course


def launch(db: Session, *, id_token: str, state: str, now: datetime | None = None) -> tuple[str, uuid.UUID]:
    """(launch code, course id) for a valid instructor launch. The caller commits."""
    now = now or datetime.now(UTC)
    login_state = _spend_state(db, state, now)
    platform = db.get(LtiPlatform, login_state.platform_id)
    claims = validate_id_token(db, platform, id_token, login_state.nonce)
    if not INSTRUCTOR_ROLES.intersection(claims.roles):
        raise LaunchRefused("not an instructor",
                            "Hiện công cụ chỉ dành cho giảng viên của khóa học. Sinh viên sẽ dùng được ở bản sau.")

    try:
        user = _user_for(db, platform, claims)
        _act_as(db, user.id)
        course = _course_for(db, platform, claims, user)
        db.flush()
    except IntegrityError as error:
        # Two launches of the same new person or course at once: one wins, the other retries.
        raise LaunchRefused(f"concurrent first launch: {error.orig}", "Hãy mở lại hoạt động từ LMS.") from error

    code = secrets.token_urlsafe(32)
    db.add(LtiLaunchCode(code_hash=_hash(code), user_id=user.id, course_id=course.id, expires_at=now + LAUNCH_CODE_TTL))
    logger.info("lti_launch platform=%s user=%s course=%s", platform.issuer, user.id, course.id)
    return code, course.id


# --- 3. Code for tokens


def exchange(db: Session, code: str, now: datetime | None = None) -> tuple[User, uuid.UUID]:
    """The user and course of a launch code, spending it. The caller issues tokens and commits."""
    now = now or datetime.now(UTC)
    spent = db.execute(
        delete(LtiLaunchCode).where(LtiLaunchCode.code_hash == _hash(code)).returning(LtiLaunchCode)
    ).scalar_one_or_none()
    if spent is None or spent.expires_at <= now:
        raise LaunchRefused("unknown, used or expired launch code", "Phiên mở công cụ đã hết hạn. Hãy mở lại hoạt động từ LMS.")
    user = db.get(User, spent.user_id)
    if user is None or not user.is_active:
        raise LaunchRefused("account disabled", "Tài khoản đã bị vô hiệu hóa. Vui lòng liên hệ admin.")
    return user, spent.course_id
