"""Requests above the limit wait instead of competing for threads and database connections."""

import asyncio

import httpx

from app.middleware import ConcurrencyLimitMiddleware


def _app():
    state = {"active": 0, "peak": 0}

    async def inner(scope, receive, send):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.02)
        state["active"] -= 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return inner, state


async def _get(app, path, count):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await asyncio.gather(*(client.get(path) for _ in range(count)))


def test_never_serves_more_than_the_limit_and_the_rest_still_complete():
    inner, state = _app()
    app = ConcurrencyLimitMiddleware(inner, max_concurrent=3, queue_timeout=5)
    responses = asyncio.run(_get(app, "/courses", 20))
    assert [r.status_code for r in responses] == [200] * 20
    assert state["peak"] == 3


def test_a_request_that_waits_too_long_gets_503_with_retry_after():
    inner, _ = _app()
    app = ConcurrencyLimitMiddleware(inner, max_concurrent=1, queue_timeout=0.001)
    responses = asyncio.run(_get(app, "/courses", 5))
    refused = [r for r in responses if r.status_code == 503]
    assert refused and refused[0].headers["retry-after"] == "5"
    assert any(r.status_code == 200 for r in responses)


def test_exempt_paths_answer_even_when_every_slot_is_taken():
    inner, state = _app()
    app = ConcurrencyLimitMiddleware(inner, max_concurrent=1, queue_timeout=5, exempt_paths=frozenset({"/health"}))
    responses = asyncio.run(_get(app, "/health", 5))
    assert [r.status_code for r in responses] == [200] * 5
    assert state["peak"] == 5
