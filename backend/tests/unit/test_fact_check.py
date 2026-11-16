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
        [("first", ["passage a"]), ("a wrong one", ["passage b"]), ("third", ["passage c"])], "gemini-fast", "vi")
    assert flags[0] is None and flags[2] is None
    assert "the passage says otherwise" in flags[1]
    assert {model for _, model, _ in seen} == {fact_check.judge_alias("gemini-fast")}
    # The single-claim criteria come first, unchanged: what follows only adds to them.
    assert all(instruction.startswith(claim_evaluation._SUPPORT_INSTRUCTION) for _, _, instruction in seen)


def test_a_statement_without_passages_is_flagged_without_asking_the_judge(monkeypatch):
    monkeypatch.setattr(claim_evaluation, "complete_structured",
                        lambda *a, **k: pytest.fail("no passage, nothing to ask"))
    assert fact_check.check_against_sources([("claim", [])], "gemini-fast", "vi")[0]


def test_a_judge_outage_flags_the_statement_instead_of_raising(monkeypatch):
    calls = []

    def flaky(claim, passages, language):
        calls.append(claim)
        if claim == "second":
            raise RuntimeError("gateway timed out")
        return SupportVerdict(supported=True, reason="")

    monkeypatch.setattr(fact_check, "_support", flaky)
    flags = fact_check.check_against_sources(
        [("first", ["a"]), ("second", ["b"]), ("third", ["c"])], "gemini-fast", "vi")
    assert flags[0] is None and flags[2] is None
    assert flags[1].startswith("Chưa kiểm được: ") and "gateway timed out" in flags[1]
    assert calls == ["first", "second", "third"]


def _item_verdicts(rejected=(), reason="the passage says otherwise"):
    def fake(prompt, response_model, **kwargs):
        import json
        claims = json.loads(prompt)["claims"]
        return response_model(verdicts=[
            {"number": c["number"], "supported": c["claim"] not in rejected,
             "reason": "" if c["claim"] not in rejected else reason} for c in claims])
    return fake


def test_items_are_judged_one_call_per_group_and_flagged_by_name(monkeypatch):
    seen = []
    fake = _item_verdicts(rejected={"a wrong bullet"})

    def counted(prompt, response_model, **kwargs):
        seen.append((requested_model(), kwargs["system_instruction"]))
        return fake(prompt, response_model, **kwargs)

    monkeypatch.setattr(claim_evaluation, "complete_structured", counted)
    flags = fact_check.check_items_against_sources([
        ([("Ý 1", "fine"), ("Ý 2", "a wrong bullet"), ("Câu 1", "fine too")], ["passage a"]),
        ([("Ý 1", "also fine")], ["passage b"]),
    ], "gemini-fast", "vi")
    assert flags == [[None, "Ý 2: the passage says otherwise", None], [None]]
    assert len(seen) == 2
    assert {model for model, _ in seen} == {fact_check.judge_alias("gemini-fast")}
    # The criteria are the single-claim judge's, not a copy that could drift from them.
    assert all(instruction.startswith(claim_evaluation._SUPPORT_INSTRUCTION) for _, instruction in seen)


def test_a_judge_outage_flags_every_item_of_the_group_instead_of_raising(monkeypatch):
    def down(*a, **k):
        raise RuntimeError("gateway timed out")

    monkeypatch.setattr(claim_evaluation, "complete_structured", down)
    flags = fact_check.check_items_against_sources([([("Ý 1", "a"), ("Câu 1", "b")], ["p"])], "gemini-fast", "vi")
    assert [f.split(":")[0] for f in flags[0]] == ["Ý 1", "Câu 1"]
    assert all("Chưa kiểm được" in f and "gateway timed out" in f for f in flags[0])


def test_items_without_passages_are_flagged_without_asking_the_judge(monkeypatch):
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda *a, **k: pytest.fail("nothing to ask"))
    assert fact_check.check_items_against_sources([([("Ý 1", "a")], [])], "gemini-fast", "vi") == [
        ["Ý 1: Chưa có đoạn nguồn để kiểm"]]


def test_items_of_an_unknown_generator_are_refused_before_any_call(monkeypatch):
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda *a, **k: pytest.fail("no call"))
    with pytest.raises(ValueError, match="mystery-1"):
        fact_check.check_items_against_sources([([("Ý 1", "a")], ["p"])], "mystery-1", "vi")


@pytest.mark.parametrize("numbers", [[1, 1], [0, 1], [1], [1, 2, 3]])
def test_a_reply_numbered_wrongly_is_not_trusted_for_any_item(monkeypatch, numbers):
    # Duplicates, 0-based, missing or extra numbers: which verdict belongs to which item is unknown.
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda prompt, response_model, **kw: response_model(
        verdicts=[{"number": n, "supported": True, "reason": ""} for n in numbers]))
    flags = fact_check.check_items_against_sources([([("Ý 1", "a"), ("Câu 1", "b")], ["p"])], "gemini-fast", "vi")
    assert [f.split(":")[0] for f in flags[0]] == ["Ý 1", "Câu 1"]
    assert all("Chưa kiểm được" in f for f in flags[0])


@pytest.mark.parametrize(("language", "named"), [("vi", "Vietnamese"), ("en", "English")])
def test_the_judge_writes_its_reason_in_the_lessons_language(monkeypatch, language, named):
    seen = []

    def fake(prompt, response_model, **kwargs):
        seen.append(kwargs["system_instruction"])
        if response_model is SupportVerdict:
            return SupportVerdict(supported=True, reason="")
        return _item_verdicts()(prompt, response_model, **kwargs)

    monkeypatch.setattr(claim_evaluation, "complete_structured", fake)
    fact_check.check_against_sources([("claim", ["passage"])], "gemini-fast", language)
    fact_check.check_items_against_sources([([("Ý 1", "a")], ["p"])], "gemini-fast", language)
    assert len(seen) == 2
    for instruction in seen:
        assert instruction.startswith(claim_evaluation._SUPPORT_INSTRUCTION)
        assert f"Write `reason` in {named}" in instruction


def test_the_judge_is_told_a_claim_may_translate_its_evidence(monkeypatch):
    # The live run flagged "thời điểm số không" (time zero) as contradicting "arrive at time 0".
    seen = []
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda prompt, response_model, **kw: (
        seen.append(kw["system_instruction"]), _item_verdicts()(prompt, response_model, **kw))[1])
    fact_check.check_items_against_sources([([("Câu 1", "a")], ["p"])], "gemini-fast", "vi")
    assert "translation" in seen[0] and "meaning, not the wording" in seen[0]
    assert '"số không" is 0' in seen[0]


def test_the_item_judge_passes_greeting_and_framing_sentences(monkeypatch):
    # Live run 2 flagged scene 1's "Chào mừng các bạn đến với bài học ..." and "Chúng ta sẽ tập trung
    # tìm hiểu hai thuật toán ...": the script prompt asks for them, so every video began flagged.
    seen = []
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda prompt, response_model, **kw: (
        seen.append(kw["system_instruction"]), _item_verdicts()(prompt, response_model, **kw))[1])
    fact_check.check_items_against_sources([([("Câu 1", "a")], ["p"])], "gemini-fast", "vi")
    assert "only greets" in seen[0] and '"Chào các bạn, hôm nay' in seen[0]
    # A framing sentence that also states something is still judged on what it states.
    assert "judged on that" in seen[0]


def test_the_card_judge_has_no_framing_rule(monkeypatch):
    # A card has no greeting: a card front that "announces" a topic is a question to be answered.
    seen = []
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda prompt, response_model, **kw: (
        seen.append(kw["system_instruction"]), SupportVerdict(supported=True, reason=""))[1])
    fact_check.check_against_sources([("claim", ["passage"])], "gemini-fast", "vi")
    assert "only greets" not in seen[0]
