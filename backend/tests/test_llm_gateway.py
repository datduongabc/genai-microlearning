"""The backend never talks to an LLM provider directly: every call goes to the
LLM gateway (LiteLLM Proxy), which is the only place that holds provider API
keys and maps a model alias to a real provider/model."""

from types import SimpleNamespace

import litellm
import pytest
from pydantic import BaseModel

from app.config import settings
from app.llm import gateway as llm


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


# --- choosing the model for one run ------------------------------------------
# A teacher picks the model per generation, so the choice travels with the run rather
# than through the signature of every function in the pipeline: ten call sites across
# generation, chat and ingestion would otherwise each grow a parameter.


def test_a_chosen_alias_overrides_the_configured_one(captured):
    with llm.using_model("groq-pro"):
        llm.complete("hello")

    assert captured["model"] == "openai/groq-pro"


def test_choosing_nothing_leaves_the_configured_alias_alone(captured):
    with llm.using_model(None):
        llm.complete("hello")

    assert captured["model"] == "openai/dev-alias"


def test_the_choice_does_not_outlive_the_block(captured):
    with llm.using_model("groq-pro"):
        pass
    llm.complete("hello")

    assert captured["model"] == "openai/dev-alias"


def test_the_choice_does_not_leak_out_of_a_block_that_raises(captured):
    """A generation run that fails must not leave the next one on someone else's model."""
    with pytest.raises(RuntimeError), llm.using_model("groq-pro"):
        raise RuntimeError("sinh that bai")
    llm.complete("hello")

    assert captured["model"] == "openai/dev-alias"


def test_a_nested_choice_restores_the_outer_one(captured):
    with llm.using_model("groq-pro"):
        with llm.using_model("claude-fast"):
            llm.complete("inner")
            assert captured["model"] == "openai/claude-fast"
        llm.complete("outer")

    assert captured["model"] == "openai/groq-pro"


class _Formula(BaseModel):
    reason: str
    ok: bool


def test_a_structured_reply_with_a_raw_control_character_in_a_string_is_still_read(monkeypatch, captured):
    """Model hay viết công thức kiểu LaTeX và để lọt ký tự điều khiển trần (tab, xuống dòng) vào
    giữa chuỗi JSON. Trình đọc nghiêm từ chối cả câu trả lời, và một bước kiểm hỏng như vậy làm
    hỏng cả lượt sinh module, dù nội dung vẫn đọc được và vẫn đúng schema."""
    reply = '{"reason": "vì \\frac{1}{x}\tvà dòng mới\nở đây", "ok": true}'

    def fake_completion(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])

    monkeypatch.setattr(litellm, "completion", fake_completion)

    result = llm.complete_structured("kiểm tra", _Formula)

    assert result.ok is True
    assert "\frac{1}{x}\tvà dòng mới\nở đây" in result.reason


def test_a_structured_reply_that_breaks_the_schema_is_still_rejected(monkeypatch, captured):
    def fake_completion(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"reason": "x"}'))])

    monkeypatch.setattr(litellm, "completion", fake_completion)

    with pytest.raises(ValueError):
        llm.complete_structured("kiểm tra", _Formula)
