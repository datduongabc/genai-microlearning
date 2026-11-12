import pytest

from app.generation import claim_evaluation, fact_check
from app.generation.claim_evaluation import SupportVerdict
from app.llm.gateway import requested_model


@pytest.mark.parametrize("generator", ["gemini-fast", "gemini-flash", "gemini-balanced", "gemini-fast-small"])
def test_the_judge_is_from_another_family_than_the_generator(generator):
    judge = fact_check.judge_alias(generator)
    assert not judge.startswith("gemini-")
    assert fact_check.alias_family(judge) != fact_check.alias_family(generator)


@pytest.mark.parametrize("generator", [
    "claude-fast", "claude-pro", "openai-fast", "openai-pro", "groq-pro", "groq-llama", "groq-qwen", "groq-minimax",
])
def test_no_generator_is_judged_by_its_own_family(generator):
    judge = fact_check.judge_alias(generator)
    assert fact_check.alias_family(judge) != fact_check.alias_family(generator)


def test_groq_serving_an_openai_model_counts_as_openai():
    assert fact_check.alias_family("groq-pro") == fact_check.alias_family("openai-fast")


def test_an_unknown_generator_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="mystery-1"):
        fact_check.judge_alias("mystery-1")


def test_fact_check_flags_what_the_judge_rejects(monkeypatch):
    seen = []

    def fake(prompt, response_model, **kwargs):
        seen.append((prompt, requested_model(), kwargs["system_instruction"]))
        ok = "wrong" not in prompt
        return SupportVerdict(supported=ok, reason="" if ok else "the passage says otherwise")

    monkeypatch.setattr(claim_evaluation, "complete_structured", fake)
    flags = fact_check.check_against_sources(
        [("first", ["passage a"]), ("a wrong one", ["passage b"]), ("third", ["passage c"])], "gemini-fast")
    assert flags[0] is None and flags[2] is None
    assert "the passage says otherwise" in flags[1]
    assert {model for _, model, _ in seen} == {fact_check.judge_alias("gemini-fast")}
    assert all(instruction == claim_evaluation._SUPPORT_INSTRUCTION for _, _, instruction in seen)


def test_a_statement_without_passages_is_flagged_without_asking_the_judge(monkeypatch):
    monkeypatch.setattr(claim_evaluation, "complete_structured",
                        lambda *a, **k: pytest.fail("no passage, nothing to ask"))
    assert fact_check.check_against_sources([("claim", [])], "gemini-fast")[0]


def test_a_judge_outage_flags_the_statement_instead_of_raising(monkeypatch):
    calls = []

    def flaky(claim, passages, options):
        calls.append(claim)
        if claim == "second":
            raise RuntimeError("gateway timed out")
        return SupportVerdict(supported=True, reason="")

    monkeypatch.setattr(fact_check, "_support", flaky)
    flags = fact_check.check_against_sources(
        [("first", ["a"]), ("second", ["b"]), ("third", ["c"])], "gemini-fast")
    assert flags[0] is None and flags[2] is None
    assert flags[1].startswith("Chưa kiểm được: ") and "gateway timed out" in flags[1]
    assert calls == ["first", "second", "third"]
