"""The signed assertion the tool trades for an access token at the LMS (Names and Role Provisioning)."""

import time
import uuid
from types import SimpleNamespace

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings
from app.lti import roster

TOKEN_URL = "https://moodle.example.edu/mod/lti/token.php"


def _assertion(monkeypatch) -> dict:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    monkeypatch.setattr(settings, "lti_private_key", pem)
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

    platform = SimpleNamespace(id=uuid.uuid4(), client_id="client-1", token_url=TOKEN_URL)
    roster._tokens.clear()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert roster._access_token(client, platform) == "t"
    return jwt.decode(sent["client_assertion"], key.public_key(), algorithms=["RS256"], audience=TOKEN_URL,
                      options={"verify_iat": False})


def test_an_lms_whose_clock_is_behind_still_accepts_the_assertion(monkeypatch):
    # Moodle refuses an iat later than its own clock, with no leeway: a Moodle one second behind
    # refused every token on 2026-11-11.
    claims = _assertion(monkeypatch)
    now = time.time()
    assert claims["iat"] <= now - 30
    assert now < claims["exp"] <= now + roster.ASSERTION_LIFETIME_SECONDS + 1
    assert claims["iss"] == claims["sub"] == "client-1"
    assert claims["jti"]
