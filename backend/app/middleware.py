"""ASGI middleware for the whole application."""

import asyncio
from collections.abc import Callable
from typing import ClassVar

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodySizeLimitMiddleware:
    """Refuses a request body over `max_bytes` with 413 before the application has read
    it. Without a limit anything can be POSTed as a "JSON body" and is held in memory
    whole. `exempt` names the requests that carry a big body on purpose (the upload
    endpoint, which enforces its own limits)."""

    def __init__(self, app: ASGIApp, max_bytes: int, exempt: Callable[[Scope], bool] = lambda scope: False):
        self.app = app
        self.max_bytes = max_bytes
        self.exempt = exempt

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.exempt(scope):
            await self.app(scope, receive, send)
            return

        if self._declared_length(scope) > self.max_bytes:
            await self._refuse(send)
            return

        received = 0
        refused = False

        async def limited_receive() -> Message:
            nonlocal received, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Answer here, and tell the application its client went away: FastAPI
                    # turns any exception raised while it reads the body into a 400, so
                    # the 413 could not be produced by raising.
                    refused = True
                    await self._refuse(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not refused:  # after a 413 the application's own reply is dropped
                await send(message)

        await self.app(scope, limited_receive, guarded_send)

    @staticmethod
    def _declared_length(scope: Scope) -> int:
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return 0
        return 0

    @staticmethod
    async def _refuse(send: Send) -> None:
        body = b'{"detail":"Request body too large"}'
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                # The rest of the body is still on the wire, so this connection is done.
                (b"connection", b"close"),
            ],
        })
        await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    """Adds the response headers that make a browser treat an API answer as plain data:
    no sniffing it into something else, no framing, no referrer. Responses under /auth
    can carry tokens, so they are never cached. In production it also pins HTTPS and
    forbids the response from loading anything (a JSON answer needs nothing)."""

    BASIC: ClassVar[dict[bytes, bytes]] = {
        b"x-content-type-options": b"nosniff",
        b"x-frame-options": b"DENY",
        b"referrer-policy": b"no-referrer",
    }
    PRODUCTION: ClassVar[dict[bytes, bytes]] = {
        b"strict-transport-security": b"max-age=31536000; includeSubDomains",
        b"content-security-policy": b"default-src 'none'; frame-ancestors 'none'",
    }

    def __init__(self, app: ASGIApp, production: bool = False):
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        wanted = dict(self.BASIC)
        if self.production:
            wanted.update(self.PRODUCTION)
        if scope["path"].startswith("/auth/"):
            wanted[b"cache-control"] = b"no-store"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend((name, value) for name, value in wanted.items() if name not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class ConcurrencyLimitMiddleware:
    """Serves at most `max_concurrent` requests at once; the others wait here, which costs no thread
    or database connection. One that waits longer than `queue_timeout` gets 503, so an overload
    sheds requests instead of stalling them all. See api_max_concurrent_requests in app/config.py."""

    def __init__(self, app, max_concurrent: int, queue_timeout: float, exempt_paths: frozenset[str] = frozenset()):
        self.app = app
        self.queue_timeout = queue_timeout
        self.exempt_paths = exempt_paths
        self._slots = asyncio.Semaphore(max_concurrent)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self.queue_timeout)
        except TimeoutError:
            response = JSONResponse({"detail": "Hệ thống đang quá tải, vui lòng thử lại sau."}, status_code=503,
                                    headers={"Retry-After": "5"})
            await response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self._slots.release()
