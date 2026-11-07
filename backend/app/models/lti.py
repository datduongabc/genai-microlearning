"""LTI 1.3: the platforms that may launch this tool, and who and what their launches map to.

None of these tables is under row level security: a launch arrives before anyone is signed in,
so there is no app.current_user_id to filter by. Only the request role has grants on them; the
worker may delete expired rows and read what it needs to keep course members in step with the LMS
(migration 0029).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.models import created_at_column, uuid_pk


class LtiPlatform(Base):
    """A registration of this tool in an LMS, entered by whoever configures both sides."""
    __tablename__ = "lti_platforms"
    __table_args__ = (UniqueConstraint("issuer", "client_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    issuer: Mapped[str]
    client_id: Mapped[str]
    auth_login_url: Mapped[str]
    jwks_url: Mapped[str]
    # Where the tool trades an assertion it signed for an access token to the LMS's services (OAuth 2
    # client credentials, app/lti/roster.py). Without it, the course's member list is never read.
    token_url: Mapped[str | None] = mapped_column(default=None)
    # The project's own Moodle: its `sub` is the Moodle user id and its context id the Moodle
    # course id, which a signed launch confirms as strongly as the plugin's confirmation flow.
    links_moodle: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = created_at_column()


class LtiDeployment(Base):
    """A placement of the registration; a launch from a deployment not listed here is refused."""
    __tablename__ = "lti_deployments"
    __table_args__ = (UniqueConstraint("platform_id", "deployment_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    platform_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lti_platforms.id", ondelete="CASCADE"))
    deployment_id: Mapped[str]


class LtiIdentity(Base):
    """(issuer, sub) is the person: the spec makes `sub` unique per platform, and email is
    neither required in a launch nor proof of anything."""
    __tablename__ = "lti_identities"
    __table_args__ = (UniqueConstraint("platform_id", "sub"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    platform_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lti_platforms.id", ondelete="CASCADE"))
    sub: Mapped[str]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = created_at_column()


class LtiContext(Base):
    """An LMS course and the course here it opens."""
    __tablename__ = "lti_contexts"
    __table_args__ = (UniqueConstraint("platform_id", "deployment_id", "context_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    platform_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lti_platforms.id", ondelete="CASCADE"))
    deployment_id: Mapped[str]
    context_id: Mapped[str]
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    # The LMS's Names and Role Provisioning address for this course, from the latest launch that carried
    # one, and when the member list was last read from it.
    memberships_url: Mapped[str | None] = mapped_column(default=None)
    roster_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = created_at_column()


class LtiLoginState(Base):
    """Between the login initiation and the launch: the state is the key, the nonce what the
    id_token must carry. Deleted when used, so neither can be replayed."""
    __tablename__ = "lti_login_states"

    state_hash: Mapped[str] = mapped_column(primary_key=True)
    nonce: Mapped[str]
    platform_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lti_platforms.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LtiLaunchCode(Base):
    """Carries a finished launch to the frontend, which trades it once for tokens. Stored hashed:
    it is a credential for its few seconds."""
    __tablename__ = "lti_launch_codes"

    code_hash: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CourseMember(Base):
    """Someone who may chat with a course's material without owning it: a student, added by their own
    LTI launch. Unlike the tables above it is under row level security (migration 0027)."""
    __tablename__ = "course_members"

    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    source: Mapped[str] = mapped_column(default="lti", server_default="lti")
    created_at: Mapped[datetime] = created_at_column()
