"""The parts of a chat stream that hold no database: taking out citation markers, keeping the connection alive, and
shedding streams past the limit."""

import asyncio

from starlette.responses import JSONResponse

from app.api.chat import _with_heartbeat
from app.chat.service import MarkerFilter
from app.middleware import ConcurrencyLimitMiddleware


def _filtered(*pieces: str) -> tuple[str, list[str]]:
    markers = MarkerFilter()
    text = "".join(markers.feed(piece) for piece in pieces) + markers.finish()
    return text, markers.source_ids


def test_a_marker_split_across_pieces_is_still_removed_and_read():
    assert _filtered("Heap là cây [", "S", "2", "] nhị phân.") == ("Heap là cây  nhị phân.", ["S2"])


def test_a_bracket_that_is_no_marker_reaches_the_reader():
    assert _filtered("mảng a[", "i] và b[0]") == ("mảng a[i] và b[0]", [])


def test_a_bracket_left_open_at_the_end_is_not_lost():
    assert _filtered("mảng a[") == ("mảng a[", [])


def test_a_long_bracket_is_released_without_waiting_for_it_to_close():
    markers = MarkerFilter()

    assert markers.feed("x [một đoạn rất dài") == "x [một đoạn rất dài"


def test_silence_sends_a_comment_and_the_events_still_arrive_in_order():
    async def slow():
        yield "first"
        await asyncio.sleep(0.05)
        yield "second"

    async def collect():
        return [event async for event in _with_heartbeat(slow(), seconds=0.01)]

    events = asyncio.run(collect())

    assert events[0] == "first"
    assert events[-1] == "second"
    assert ": ping\n\n" in events


def test_a_reader_leaving_stops_the_stream_behind_it():
    stopped = []

    async def endless():
        try:
            while True:
                await asyncio.sleep(0.01)
                yield "event"
        finally:
            stopped.append(True)

    async def leave_after_one():
        stream = _with_heartbeat(endless(), seconds=1)
        await anext(stream)
        await stream.aclose()

    asyncio.run(leave_after_one())

    assert stopped == [True]


def _request(path: str) -> dict:
    return {"type": "http", "path": path, "method": "POST", "headers": [], "query_string": b""}


def test_streams_past_the_limit_are_refused_at_once_and_a_finished_one_frees_its_place():
    async def scenario():
        release = asyncio.Event()

        async def app(scope, receive, send):
            await release.wait()
            await JSONResponse({})(scope, receive, send)

        limit = ConcurrencyLimitMiddleware(app, max_concurrent=1, queue_timeout=0.01, max_streams=1)
        sent = []

        async def send(message):
            sent.append(message)

        first = asyncio.create_task(limit(_request("/courses/x/chat/stream"), None, send))
        await asyncio.sleep(0)
        await limit(_request("/courses/x/chat/stream"), None, send)
        refused = sent[0]["status"]
        release.set()
        await first
        sent.clear()
        await limit(_request("/courses/x/chat/stream"), None, send)
        return refused, sent[0]["status"]

    assert asyncio.run(scenario()) == (503, 200)


def test_an_open_stream_does_not_take_a_slot_from_other_requests():
    async def scenario():
        hold = asyncio.Event()

        async def app(scope, receive, send):
            if scope["path"].endswith("/stream"):
                await hold.wait()
            await JSONResponse({})(scope, receive, send)

        limit = ConcurrencyLimitMiddleware(app, max_concurrent=1, queue_timeout=0.01, max_streams=5)
        sent = []

        async def send(message):
            sent.append(message)

        stream = asyncio.create_task(limit(_request("/courses/x/chat/stream"), None, send))
        await asyncio.sleep(0)
        await limit(_request("/courses"), None, send)
        status = sent[0]["status"]
        hold.set()
        await stream
        return status

    assert asyncio.run(scenario()) == 200
