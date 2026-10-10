import pytest

from app.study.judge import check_packet_budget
from tests.integration.judge_helpers import _packet


def test_groq_packet_budget_reserves_output_and_schema_and_refuses_without_truncation(monkeypatch):
    seen = []

    def tokens(**kwargs):
        seen.append(kwargs)
        return 6000 if "messages" in kwargs else 500

    monkeypatch.setattr("app.study.judge.litellm.token_counter", tokens)
    packet = _packet()
    before = packet.model_dump()
    with pytest.raises(ValueError, match="not truncated"):
        check_packet_budget(packet, "groq/openai/gpt-oss-120b")
    assert packet.model_dump() == before
    assert "messages" in seen[0] and "text" in seen[1]
    check_packet_budget(packet, "anthropic/claude-sonnet-5")
    assert len(seen) == 2


def test_small_groq_packet_is_accepted():
    check_packet_budget(_packet(), "groq/openai/gpt-oss-120b")
