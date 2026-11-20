"""LTI 1.3 endpoints. Under /api/lti through the proxy (Caddy, CloudFront), like the rest of the API.

Register the tool in the LMS with:
    Initiate login URL   {PUBLIC_URL}/api/lti/login
    Redirection URI      {PUBLIC_URL}/api/lti/launch
    Tool URL             {PUBLIC_URL}/api/lti/launch
    Public keyset URL    {PUBLIC_URL}/api/lti/jwks
and the platform in this tool with scripts/register_lti_platform.py (see docs/integration/lti.md).
"""

import html
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import Token, UserResponse
from app.api.deps import is_course_author
from app.auth import audit, sessions
from app.auth.audit import log_auth_event
from app.auth.rate_limit import limiter
from app.auth.security import create_access_token
from app.config import settings
from app.db import get_db
from app.lti import launch as lti
from app.lti import roster
from app.models import Course

router = APIRouter(tags=["lti"])
logger = logging.getLogger(__name__)


def _refused(request: Request, refused: lti.LaunchRefused) -> HTMLResponse:
    logger.warning("lti_refused path=%r reason=%s", request.url.path, refused.reason)
    # A page, not JSON: the person is looking at it inside the LMS.
    body = (
        "<!doctype html><html lang=\"vi\"><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Không mở được công cụ</title>"
        "<body style=\"font-family:system-ui,sans-serif;max-width:36rem;margin:3rem auto;padding:0 1rem\">"
        f"<h1 style=\"font-size:1.25rem\">Không mở được công cụ</h1><p>{html.escape(refused.message)}</p>"
        "</body></html>"
    )
    # Shown inside the LMS's iframe, so any page may frame it: it has nothing to click.
    return HTMLResponse(body, status_code=400, headers={
        "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors *",
    })


async def _params(request: Request) -> dict[str, str]:
    """The login initiation may come as a GET or a form POST, as the spec allows both."""
    params = dict(request.query_params)
    if request.method == "POST":
        params.update({k: v for k, v in (await request.form()).items() if isinstance(v, str)})
    return params


# Moodle may start the login with either method. One route for both would give the OpenAPI schema
# the same operation id twice, so each method is registered on its own.
@router.get("/lti/login", operation_id="lti_login_get")
@router.post("/lti/login", operation_id="lti_login_post")
@limiter.limit("60/minute")
async def lti_login(request: Request, db: Session = Depends(get_db)):
    params = await _params(request)
    if not params.get("iss") or not params.get("login_hint"):
        return _refused(request, lti.LaunchRefused("login without iss or login_hint"))
    try:
        url = lti.login(
            db, issuer=params["iss"], login_hint=params["login_hint"],
            target_link_uri=params.get("target_link_uri"), client_id=params.get("client_id"),
            lti_message_hint=params.get("lti_message_hint"),
        )
    except lti.LaunchRefused as refused:
        return _refused(request, refused)
    db.commit()
    return RedirectResponse(url, status_code=302)


@router.post("/lti/launch")
@limiter.limit("60/minute")
def lti_launch(request: Request, id_token: str = Form(max_length=20_000), state: str = Form(max_length=200),
               db: Session = Depends(get_db)):
    try:
        code, course_id = lti.launch(db, id_token=id_token, state=state)
    except lti.LaunchRefused as refused:
        # Undoes a half-made account or course, then spends the state again: a refused id_token
        # must not be postable a second time, whatever the reason it was refused.
        db.rollback()
        lti.discard_state(db, state)
        db.commit()
        return _refused(request, refused)
    db.commit()
    # A fragment, not a query: it is not sent to any server, so it reaches no access log.
    # 303 turns the LMS's POST into a GET of the frontend page.
    return RedirectResponse(f"{settings.public_url.rstrip('/')}/lti/launch#code={code}&course={course_id}",
                            status_code=303)


class ExchangeRequest(BaseModel):
    code: str = Field(max_length=200)


class LtiToken(Token):
    course_id: str
    # "member" when the launch opened someone else's course to chat in: the frontend opens the chat only,
    # whatever else the account may do.
    course_role: Literal["owner", "member"]


@router.post("/lti/session", response_model=LtiToken)
@limiter.limit("30/minute")
def lti_session(request: Request, payload: ExchangeRequest, db: Session = Depends(get_db)):
    try:
        user, course_id = lti.exchange(db, payload.code)
    except lti.LaunchRefused as refused:
        logger.warning("lti_refused path=%r reason=%s", request.url.path, refused.reason)
        raise HTTPException(status_code=401, detail=refused.message) from None
    refresh_token = sessions.start_session(db, user.id)
    db.commit()
    lti.act_as(db, user.id)
    course = db.get(Course, course_id)
    log_auth_event(audit.LOGIN_SUCCESS, request, user_id=user.id, email=user.email, detail={"via": "lti"})
    return LtiToken(
        access_token=create_access_token(subject=user.id), refresh_token=refresh_token, course_id=str(course_id),
        # "owner" opens the authoring page: the owner and a teacher of the course both write under it.
        course_role="owner" if course is not None and is_course_author(db, course, user) else "member",
        user=UserResponse.of(user),
    )


@router.get("/lti/jwks")
def lti_jwks():
    """The tool's public key, with which the LMS checks the assertion the tool trades for an access
    token to read a course's member list (app/lti/roster.py). Empty without LTI_PRIVATE_KEY."""
    return roster.public_jwks()
