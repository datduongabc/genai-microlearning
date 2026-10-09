"""The backend never talks to an LLM provider directly: every call goes to the
LLM gateway (LiteLLM Proxy), which is the only place that holds provider API
keys and maps a model alias to a real provider/model."""

import json
from types import SimpleNamespace

import httpx
import litellm
import pytest
from openai import OpenAI
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
    monkeypatch.setattr(settings, "llm_model", "configured-alias")
    return sent


def test_a_completion_goes_to_the_gateway_with_the_gateway_key(captured):
    llm.complete("hello")

    assert captured["api_base"] == "http://gateway.test:4000"
    assert captured["api_key"] == "sk-gateway-test"


def test_the_configured_alias_is_what_is_requested(captured):
    llm.complete("hello")

    assert captured["model"] == "openai/configured-alias"


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


def test_audio_transcription_also_goes_through_the_gateway(monkeypatch, captured, tmp_path):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"audio")
    segments = [{"start": 0, "end": 1, "text": "hello"}]
    def transcription(**kwargs):
        captured.update(kwargs)
        assert kwargs["file"].read() == b"audio"
        return SimpleNamespace(segments=segments)
    monkeypatch.setattr(litellm, "transcription", transcription)
    with llm.using_model("groq-pro"):
        assert llm.transcribe_audio(str(path)) == segments
    assert captured["api_base"] == "http://gateway.test:4000"
    assert captured["api_key"] == "sk-gateway-test"
    assert captured["model"] == "openai/transcribe"
    assert captured["response_format"] == "verbose_json"
    assert captured["max_retries"] == 0
    assert captured["file"].closed


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

    assert captured["model"] == "openai/configured-alias"


def test_the_choice_does_not_outlive_the_block(captured):
    with llm.using_model("groq-pro"):
        pass
    llm.complete("hello")

    assert captured["model"] == "openai/configured-alias"


def test_the_choice_does_not_leak_out_of_a_block_that_raises(captured):
    """A generation run that fails must not leave the next one on someone else's model."""
    with pytest.raises(RuntimeError), llm.using_model("groq-pro"):
        raise RuntimeError("sinh that bai")
    llm.complete("hello")

    assert captured["model"] == "openai/configured-alias"


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


def test_experiment_mode_pins_and_survives_nested_model_choices(captured):
    with llm.experiment_mode():
        with llm.using_model("gemini-fast"):
            llm.complete("hello", small_step=True)
        assert captured["model"] == "openai/gemini-fast"
        assert captured["extra_body"]["disable_fallbacks"] is True
    llm.complete("hello")
    assert not captured.get("extra_body", {}).get("disable_fallbacks", False)


def test_successful_calls_record_the_served_model_not_the_requested_alias(monkeypatch, captured):
    def fake(**kwargs):
        return SimpleNamespace(model="gemini-3.5-flash", choices=[
            SimpleNamespace(message=SimpleNamespace(content='{"value": 42}')),
        ])
    monkeypatch.setattr(litellm, "completion", fake)
    with llm.capture_models() as calls, llm.using_model("gemini-fast"):
        assert llm.complete_structured("hello", _Answer).value == 42
    assert calls == [{"requested_alias": "gemini-fast", "routed_alias": "gemini-fast",
                      "served_model": "gemini-3.5-flash"}]
    with llm.capture_models() as next_calls:
        assert next_calls == []


@pytest.mark.parametrize("alias,small_step,pinned,served", [
    ("gemini-balanced", False, False, "gemini-3.5-flash"),
    ("gemini-balanced", False, False, "gemini-3.5-flash-lite"),
    ("gemini-fast", True, False, "openai/gpt-oss-120b"),
    ("gemini-fast", False, True, "gemini-3.5-flash-lite"),
])
def test_served_model_survives_the_http_response(monkeypatch, alias, small_step, pinned, served):
    """Exercise the SDK wire contract: the proxy returns aliases unless raw names are requested."""
    sent = []

    def proxy(request):
        body = json.loads(request.content)
        sent.append(body)
        raw = body.get("metadata", {}).get("_complexity_router_return_raw_model_name") is True
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
            "model": served if raw else body["model"],
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": '{"value": 42}'}}],
        })

    monkeypatch.setattr(settings, "llm_experiment_mode", pinned)
    with (
        OpenAI(api_key="test-key", base_url="http://gateway.test/v1",
               http_client=httpx.Client(transport=httpx.MockTransport(proxy))) as client,
        llm.capture_models() as calls,
        llm.using_model(alias),
    ):
        result = llm.complete_structured(
            "hello", _Answer, small_step=small_step, client=client,
            extra_body={"metadata": {"run": "test"}},
        )

    routed = f"{alias}-small" if small_step else alias
    assert result.value == 42
    assert calls == [{"requested_alias": alias, "routed_alias": routed, "served_model": served}]
    assert sent[0]["metadata"]["run"] == "test"
    assert sent[0].get("disable_fallbacks", False) is pinned


def test_only_bounded_small_steps_use_the_small_fallback_profile(captured):
    with llm.using_model("gemini-fast"):
        llm.complete("hello", small_step=True)
        assert captured["model"] == "openai/gemini-fast-small"
        assert captured["max_tokens"] == 2048
        llm.complete("long prompt " * 10000, small_step=True)
        assert captured["model"] == "openai/gemini-fast"
        llm.complete("Content even without sources")
        assert captured["model"] == "openai/gemini-fast"


def test_environment_experiment_switch_disables_fallbacks(monkeypatch, captured):
    monkeypatch.setattr(settings, "llm_experiment_mode", True)
    with llm.using_model("gemini-fast"):
        llm.complete("hello", small_step=True)
    assert captured["model"] == "openai/gemini-fast"
    assert captured["extra_body"]["disable_fallbacks"] is True


def test_small_profile_respects_schema_and_output_budget(captured):
    with llm.using_model("gemini-fast"):
        llm.complete("hello", small_step=True, max_tokens=4096)
        assert captured["model"] == "openai/gemini-fast"
        llm.complete("hello", small_step=True, response_format={"description": "schema " * 10000})
        assert captured["model"] == "openai/gemini-fast"


def test_experiment_rejects_a_balanced_alias(captured):
    with llm.experiment_mode(), llm.using_model("gemini-balanced"), pytest.raises(ValueError):
        llm.complete("hello")
    assert captured == {}
