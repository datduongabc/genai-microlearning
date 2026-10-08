"""OWASP A07 (Identification & Authentication Failures) — chống brute-force
trên /auth/login bằng rate limit theo IP."""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.auth.security import decode_token, token_user_id

# Counters live in the memory of the backend process, which runs alone: they are exact
# and reset on a restart. key_style="endpoint": slowapi counts per URL by default, and an
# endpoint such as /courses/{id}/chat has a different URL for every course, so each course
# would get its own budget and a user could get past any limit by changing the id.
limiter = Limiter(key_func=get_remote_address, key_style="endpoint", storage_uri="memory://")


def user_or_address(request: Request) -> str:
    """Rate limit key for endpoints behind a login: the user, not the address. People who
    share an address (a campus network, or every request that reaches the container
    through the Docker gateway) must not share a bucket, and one person must not be able
    to escape a limit by changing address. Without a valid token it falls back to the
    address, which is all there is to tell such a client apart."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        user_id = token_user_id(decode_token(token))
        if user_id is not None:
            return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"
