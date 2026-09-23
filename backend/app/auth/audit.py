"""Authentication events go to the application log: what happened, to which account, from
where.

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
