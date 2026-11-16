"""The price of a chat call comes back from the gateway in `x-litellm-response-cost`: LiteLLM reads
OpenRouter's own `usage.cost` (it asks for it on every OpenRouter call) and falls back to its price
map. Only the HTTP layer is faked here, so the header handling is the real one."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import timing
from app.config import settings
from app.llm import gateway


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
