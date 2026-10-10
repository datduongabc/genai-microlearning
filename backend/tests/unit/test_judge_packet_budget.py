import httpx
import litellm
import pytest
from litellm.litellm_core_utils.exception_mapping_utils import exception_type

from app.study.judge import check_packet_budget, is_context_window_error
from tests.integration.judge_helpers import _packet


def test_packet_over_the_gateway_input_cap_is_refused_without_truncation(monkeypatch):
    seen = []

    def tokens(**kwargs):
        seen.append(kwargs)
        return 3953

    monkeypatch.setattr("app.study.judge.litellm.token_counter", tokens)
    packet = _packet()
    before = packet.model_dump()
    with pytest.raises(ValueError, match="Packet too large.*3953.*3952.*not truncated"):
        check_packet_budget(packet, 3952)
    assert packet.model_dump() == before
    # The gateway's pre-call check counts the messages with no model-specific tokenizer.
    assert set(seen[0]) == {"messages"}
    check_packet_budget(packet, 3953)
    check_packet_budget(packet, None)
    assert len(seen) == 2


def test_small_packet_fits_the_groq_cap():
    check_packet_budget(_packet(), 3952)


def test_vietnamese_packet_is_counted_like_the_gateway_not_like_gpt_4o():
    packet = _packet()
    packet.content_markdown = "Cookie là một mẩu dữ liệu nhỏ mà máy chủ gửi cho trình duyệt. " * 110
    with pytest.raises(ValueError, match="Packet too large"):
        check_packet_budget(packet, 3952)


def test_gateway_context_window_rejection_is_recognised_but_other_errors_are_not():
    message = ("litellm.ContextWindowExceededError: litellm._pre_call_checks: Context Window exceeded "
               "for given call. No models have context window large enough for this call.")
    response = httpx.Response(400, request=httpx.Request("POST", "http://gateway/chat/completions"))
    with pytest.raises(litellm.BadRequestError) as rejected:
        exception_type(
            model="openai/groq-pro", custom_llm_provider="openai",
            original_exception=litellm.llms.openai.common_utils.OpenAIError(
                status_code=400, message=message, request=response.request, response=response),
        )
    assert is_context_window_error(rejected.value)
    assert is_context_window_error(
        litellm.ContextWindowExceededError("too long", model="gpt-oss-120b", llm_provider="groq"))
    assert not is_context_window_error(
        litellm.BadRequestError("bad schema", model="gpt-oss-120b", llm_provider="groq"))
    assert not is_context_window_error(
        litellm.RateLimitError("quota", llm_provider="groq", model="gpt-oss-120b"))
