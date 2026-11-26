"""Span thời gian của một lượt sinh: mỗi giai đoạn một span, mỗi lệnh gọi bên trong gắn vào giai
đoạn đang chạy, và ngoài một lượt đo thì không ghi gì (worker chạy nhiều lượt cùng lúc)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import timing
from app.config import settings
from app.generation.media_common import llm_cost
from app.llm import gateway


def test_calls_are_attributed_to_the_stage_that_made_them():
    with timing.capture_timings() as spans:
        with timing.stage("content"), timing.timed("llm", completion_tokens=7):
            pass
        with timing.timed("rerank", candidates=30):
            pass

    assert [(s["kind"], s["stage"]) for s in spans] == [("llm", "content"), ("stage", "content"), ("rerank", None)]
    assert spans[0]["completion_tokens"] == 7
    assert spans[2]["candidates"] == 30
    assert all(s["seconds"] >= 0 for s in spans)


def test_nothing_is_recorded_outside_a_capture():
    with timing.stage("content"), timing.timed("llm"):
        pass

    with timing.capture_timings() as spans:
        pass
    assert spans == []


def test_a_stage_that_raises_is_still_recorded_and_marked_failed():
    with timing.capture_timings() as spans, pytest.raises(RuntimeError), timing.stage("slides"):
        raise RuntimeError("provider down")

    assert spans == [{"kind": "stage", "stage": "slides", "seconds": spans[0]["seconds"], "failed": True}]


def test_summary_totals_seconds_and_tokens_per_stage():
    spans = [
        {"kind": "llm", "stage": "content", "seconds": 2.0, "prompt_tokens": 100, "completion_tokens": 50},
        {"kind": "llm", "stage": "content", "seconds": 1.0, "prompt_tokens": 10, "completion_tokens": 5},
        {"kind": "rerank", "stage": "content", "seconds": 0.5},
        {"kind": "stage", "stage": "content", "seconds": 4.0},
    ]

    summary = timing.summarize(spans)

    assert summary["content"] == {
        "seconds": 4.0, "llm_calls": 2, "llm_seconds": 3.0, "prompt_tokens": 110, "completion_tokens": 55,
        "rerank_seconds": 0.5,
    }


def test_profile_statistics_use_nearest_rank_percentiles():
    from scripts.profile_generation import _thong_ke

    stats = _thong_ke([float(x) for x in range(1, 21)])

    assert stats == {"n": 20, "mean": 10.5, "p50": 11.0, "p95": 19.0, "max": 20.0}
    assert _thong_ke([]) == {"n": 0}


def _call(usd, prompt=100, completion=10):
    return {"kind": "llm", "prompt_tokens": prompt, "completion_tokens": completion, "cost_usd": usd}


def test_the_cost_adds_every_call():
    cost = llm_cost([_call(0.01), _call(0.02, 200, 20), {"kind": "stage"}])
    assert cost == {"usd": 0.03, "calls": 2, "priced_calls": 2, "prompt_tokens": 300, "completion_tokens": 30}


def test_a_call_without_a_price_is_counted_but_not_guessed():
    cost = llm_cost([_call(0.01), _call(None)])
    assert cost["usd"] == 0.01 and cost["calls"] == 2 and cost["priced_calls"] == 1


def test_no_priced_call_leaves_the_amount_empty():
    assert llm_cost([_call(None)])["usd"] is None
    assert llm_cost([])["usd"] is None


def _serve(cost_header: str | None):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            body = json.dumps({
                "id": "x", "object": "chat.completion", "created": 1, "model": "google/gemini",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
            }).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            if cost_header is not None:
                self.send_header("x-litellm-response-cost", cost_header)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _span_of_one_call(monkeypatch, cost_header):
    server = _serve(cost_header)
    monkeypatch.setattr(settings, "llm_gateway_url", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(settings, "llm_gateway_key", "test-key")
    try:
        with timing.capture_timings() as spans:
            assert gateway.complete("hello") == "hi"
    finally:
        server.shutdown()
    return next(s for s in spans if s["kind"] == "llm")


def test_the_cost_of_a_call_is_read_from_the_gateway_header(monkeypatch):
    span = _span_of_one_call(monkeypatch, "0.000123")
    assert span["cost_usd"] == pytest.approx(0.000123)
    assert span["prompt_tokens"] == 30


@pytest.mark.parametrize("header", [None, "None", "", "nan", "-1", "oops"])
def test_a_missing_or_unusable_cost_is_unknown_not_zero(monkeypatch, header):
    assert _span_of_one_call(monkeypatch, header)["cost_usd"] is None
