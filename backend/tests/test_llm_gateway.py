"""The backend never talks to an LLM provider directly: every call goes to the
LLM gateway (LiteLLM Proxy), which is the only place that holds provider API
keys and maps a model alias to a real provider/model."""

from types import SimpleNamespace

import litellm
import pytest
from pydantic import BaseModel

from app.config import settings
from app.services import llm


@pytest.fixture
def captured(monkeypatch):
    """Replaces the network call and records what the backend asked for."""
    sent: dict = {}

    def fake_completion(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setattr(settings, "llm_gateway_url", "http://gateway.test:4000")
    monkeypatch.setattr(settings, "llm_gateway_key", "sk-gateway-test")
    monkeypatch.setattr(settings, "llm_model_dev", "dev-alias")
    monkeypatch.setattr(settings, "llm_model_demo", "demo-alias")
    monkeypatch.setattr(settings, "llm_use_demo_model", False)
    return sent


def test_a_completion_goes_to_the_gateway_with_the_gateway_key(captured):
    llm.complete("hello")

    assert captured["api_base"] == "http://gateway.test:4000"
    assert captured["api_key"] == "sk-gateway-test"


def test_the_configured_alias_is_what_is_requested(captured):
    llm.complete("hello")

    assert "dev-alias" in captured["model"]
    assert "demo-alias" not in captured["model"]


def test_the_demo_switch_selects_the_demo_alias(captured, monkeypatch):
    monkeypatch.setattr(settings, "llm_use_demo_model", True)

    llm.complete("hello")

    assert "demo-alias" in captured["model"]


def test_the_reply_text_is_returned(monkeypatch, captured):
    monkeypatch.setattr(
        litellm,
        "completion",
        lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="the answer"))]),
    )

    assert llm.complete("question") == "the answer"


def test_the_system_instruction_is_sent_before_the_prompt(captured):
    llm.complete("the prompt", system_instruction="be brief")

    assert captured["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "the prompt"},
    ]


class _Answer(BaseModel):
    value: int


def test_structured_completion_requests_the_schema_and_parses_the_reply(monkeypatch, captured):
    def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"value": 42}'))])

    monkeypatch.setattr(litellm, "completion", fake_completion)

    result = llm.complete_structured("what is the answer", _Answer)

    assert result == _Answer(value=42)
    assert captured["response_format"] is _Answer
    assert captured["api_base"] == "http://gateway.test:4000"


def test_audio_transcription_also_goes_through_the_gateway(captured):
    llm.transcribe_audio("AAAA", "mp3", "transcribe this")

    assert captured["api_base"] == "http://gateway.test:4000"
    parts = captured["messages"][0]["content"]
    assert {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "mp3"}} in parts
