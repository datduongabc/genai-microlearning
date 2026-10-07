"""Authentication events and the actions of admins go to the application log: what happened, to
which account, from where.

Never pass a password, a token or a hash: what is logged is exactly the arguments below,
plus the address and user agent of the request."""

import json
import logging
import uuid

from fastapi import Request

logger = logging.getLogger("app.audit")

REGISTER = "register"
REGISTER_REJECTED = "register_rejected"
LOGIN_SUCCESS = "login_success"
LOGIN_FAILED = "login_failed"
LOGOUT = "logout"
REFRESH_REJECTED = "refresh_rejected"
MOODLE_ACCOUNT_LINKED = "moodle_account_linked"
MOODLE_ACCOUNT_LINK_REFUSED = "moodle_account_link_refused"

ADMIN_CREATE_USER = "admin_create_user"
ADMIN_DELETE_USER = "admin_delete_user"
ADMIN_DISABLE_USER = "admin_disable_user"
ADMIN_ENABLE_USER = "admin_enable_user"
ADMIN_RESET_PASSWORD = "admin_reset_password"
ADMIN_RETRY_JOBS = "admin_retry_jobs"

MAX_USER_AGENT_CHARS = 200


def log_auth_event(
    event: str,
    request: Request,
    *,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
    detail: dict | None = None,
) -> None:
    ip = request.client.host if request.client else None
    user_agent = (request.headers.get("user-agent") or "")[:MAX_USER_AGENT_CHARS] or None
    # %r on client-supplied text: a newline in it must not be able to start a fake line.
    logger.info(
        "auth_event event=%s user_id=%s email=%r ip=%s user_agent=%r detail=%s",
        event, user_id, email, ip, user_agent, json.dumps(detail or {}, sort_keys=True),
    )


def log_admin_event(
    event: str,
    request: Request,
    *,
    actor_id: uuid.UUID,
    target_user_id: uuid.UUID | None = None,
    detail: dict | None = None,
) -> None:
    """An action an admin took on other people's accounts or jobs: who did it, to whom."""
    ip = request.client.host if request.client else None
    logger.info(
        "admin_event event=%s actor_id=%s target_user_id=%s ip=%s detail=%s",
        event, actor_id, target_user_id, ip, json.dumps(detail or {}, sort_keys=True),
    )
