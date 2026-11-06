"""The parts of a chat stream that hold no database: taking out citation markers, keeping the connection alive, and
shedding streams past the limit."""

import asyncio

import pytest
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


@pytest.mark.parametrize("marker", ["【S5】", "[ S5 ]", "【 S5 】", "[Nguồn: S5]"])
def test_the_marker_shapes_a_real_model_wrote_are_read(marker):
    assert _filtered("Câu một", marker[:2], marker[2:], ".") == ("Câu một.", ["S5"])


def test_one_marker_can_name_several_sources():
    assert _filtered("Câu một[S1, S2].") == ("Câu một.", ["S1", "S2"])


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


# --- Answer language: the question's, not the sources' or the history's.

@pytest.mark.parametrize("question, language", [
    ("Tiến trình là gì?", "vi"),
    ("cho em hoi tien trinh la gi", "vi"),  # Vietnamese typed without diacritics
    ("What is a process?", "en"),
    ("Explain the difference between a process and a thread", "en"),
    ("And deadlocks?", "en"),
    ("RAID 5?", None),  # nothing to tell by: the model follows the question
])
def test_the_question_language_is_read_from_the_question(question, language):
    from app.chat.service import question_language

    assert question_language(question) == language


def test_an_english_question_after_vietnamese_turns_asks_for_english_last():
    from app.chat.service import NOT_COVERED_EN, _answer_prompt
    from app.models import Chunk

    chunk = Chunk(content="Tiến trình là chương trình đang chạy.", source_ref="{}")
    history = [{"role": "user", "content": "Tiến trình là gì?"},
               {"role": "assistant", "content": "Tiến trình là chương trình đang chạy."}]

    prompt = _answer_prompt("What is a process?", [chunk], history)

    language = prompt.rsplit("NGÔN NGỮ:", 1)[1]
    assert prompt.rstrip().endswith(language.rstrip()), "the language line comes after the sources and history"
    assert "Answer in English" in language and NOT_COVERED_EN in language


def test_with_nothing_found_the_reply_is_in_the_question_language():
    import asyncio

    from app.chat.service import NOT_COVERED, NOT_COVERED_EN, answer_question, stream_answer

    async def collect(question):
        return [part async for part in stream_answer(question, [], [])]

    assert asyncio.run(collect("What is paging?"))[0] == NOT_COVERED_EN
    assert asyncio.run(collect("Phân trang là gì?"))[0] == NOT_COVERED
    assert answer_question("What is paging?", [], []).answer == NOT_COVERED_EN


# --- The answer is told what a follow-up means, not only how it was typed.

def test_the_rewritten_question_reaches_the_answer_beside_the_one_asked():
    from app.chat.service import _answer_prompt
    from app.models import Chunk

    chunk = Chunk(content="Luồng là đơn vị lập lịch nhỏ nhất.", source_ref="{}")
    history = [{"role": "user", "content": "Tiến trình là gì?"},
               {"role": "assistant", "content": "Tiến trình là chương trình đang chạy."}]

    prompt = _answer_prompt("con luong thi sao", [chunk], history, standalone="Luồng (thread) là gì?")

    asked = prompt.rsplit("CÂU HỎI:", 1)[1]
    assert asked.lstrip().startswith("con luong thi sao")
    assert "CÂU HỎI VIẾT ĐẦY ĐỦ" in asked and "Luồng (thread) là gì?" in asked
    assert "Câu hỏi bằng tiếng Việt" in prompt.rsplit("NGÔN NGỮ:", 1)[1], "the language is the asked question's"


@pytest.mark.parametrize("standalone", [None, "", "Heap là gì?", "  Heap là gì?  "])
def test_a_question_that_needed_no_rewrite_is_not_repeated(standalone):
    from app.chat.service import _answer_prompt
    from app.models import Chunk

    prompt = _answer_prompt("Heap là gì?", [Chunk(content="Heap.", source_ref="{}")], [], standalone=standalone)

    assert "CÂU HỎI VIẾT ĐẦY ĐỦ" not in prompt
