import pytest

from app.generation import claim_evaluation, fact_check
from app.generation.claim_evaluation import SupportVerdict
from app.llm.gateway import requested_model


@pytest.mark.parametrize("generator,judge", [
    ("gemini-flash", "claude-fast"),
    ("claude-fast", "openai-fast"),
    ("openai-fast", "claude-fast"),
    ("deepseek-flash", "claude-fast"),
])
def test_each_generator_is_judged_by_the_first_judge_of_another_family(generator, judge):
    assert fact_check.judge_alias(generator) == judge
    assert fact_check.alias_family(judge) != fact_check.alias_family(generator)


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
        [("first", ["passage a"]), ("a wrong one", ["passage b"]), ("third", ["passage c"])], "gemini-flash", "vi")
    assert flags[0] is None and flags[2] is None
    assert "the passage says otherwise" in flags[1]
    assert {model for _, model, _ in seen} == {fact_check.judge_alias("gemini-flash")}
    # The single-claim criteria come first, unchanged: what follows only adds to them.
    assert all(instruction.startswith(claim_evaluation._SUPPORT_INSTRUCTION) for _, _, instruction in seen)


def test_a_statement_without_passages_is_flagged_without_asking_the_judge(monkeypatch):
    monkeypatch.setattr(claim_evaluation, "complete_structured",
                        lambda *a, **k: pytest.fail("no passage, nothing to ask"))
    assert fact_check.check_against_sources([("claim", [])], "gemini-flash", "vi")[0]


def test_a_judge_outage_flags_the_statement_instead_of_raising(monkeypatch):
    calls = []

    def flaky(claim, passages, language):
        calls.append(claim)
        if claim == "second":
            raise RuntimeError("gateway timed out")
        return SupportVerdict(supported=True, reason="")

    monkeypatch.setattr(fact_check, "_support", flaky)
    flags = fact_check.check_against_sources(
        [("first", ["a"]), ("second", ["b"]), ("third", ["c"])], "gemini-flash", "vi")
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
    ], "gemini-flash", "vi")
    assert flags == [[None, "Ý 2: the passage says otherwise", None], [None]]
    assert len(seen) == 2
    assert {model for model, _ in seen} == {fact_check.judge_alias("gemini-flash")}
    # The criteria are the single-claim judge's, not a copy that could drift from them.
    assert all(instruction.startswith(claim_evaluation._SUPPORT_INSTRUCTION) for _, instruction in seen)


def test_a_judge_outage_flags_every_item_of_the_group_instead_of_raising(monkeypatch):
    def down(*a, **k):
        raise RuntimeError("gateway timed out")

    monkeypatch.setattr(claim_evaluation, "complete_structured", down)
    flags = fact_check.check_items_against_sources([([("Ý 1", "a"), ("Câu 1", "b")], ["p"])], "gemini-flash", "vi")
    assert [f.split(":")[0] for f in flags[0]] == ["Ý 1", "Câu 1"]
    assert all("Chưa kiểm được" in f and "gateway timed out" in f for f in flags[0])


def test_items_without_passages_are_flagged_without_asking_the_judge(monkeypatch):
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda *a, **k: pytest.fail("nothing to ask"))
    assert fact_check.check_items_against_sources([([("Ý 1", "a")], [])], "gemini-flash", "vi") == [
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
    flags = fact_check.check_items_against_sources([([("Ý 1", "a"), ("Câu 1", "b")], ["p"])], "gemini-flash", "vi")
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
    fact_check.check_against_sources([("claim", ["passage"])], "gemini-flash", language)
    fact_check.check_items_against_sources([([("Ý 1", "a")], ["p"])], "gemini-flash", language)
    assert len(seen) == 2
    for instruction in seen:
        assert instruction.startswith(claim_evaluation._SUPPORT_INSTRUCTION)
        assert f"Write `reason` in {named}" in instruction


def test_the_judge_is_told_a_claim_may_translate_its_evidence(monkeypatch):
    # The live run flagged "thời điểm số không" (time zero) as contradicting "arrive at time 0".
    seen = []
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda prompt, response_model, **kw: (
        seen.append(kw["system_instruction"]), _item_verdicts()(prompt, response_model, **kw))[1])
    fact_check.check_items_against_sources([([("Câu 1", "a")], ["p"])], "gemini-flash", "vi")
    assert "translation" in seen[0] and "meaning, not the wording" in seen[0]
    assert '"số không" is 0' in seen[0]


def test_the_item_judge_passes_greeting_and_framing_sentences(monkeypatch):
    # Live run 2 flagged scene 1's "Chào mừng các bạn đến với bài học ..." and "Chúng ta sẽ tập trung
    # tìm hiểu hai thuật toán ...": the script prompt asks for them, so every video began flagged.
    seen = []
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda prompt, response_model, **kw: (
        seen.append(kw["system_instruction"]), _item_verdicts()(prompt, response_model, **kw))[1])
    fact_check.check_items_against_sources([([("Câu 1", "a")], ["p"])], "gemini-flash", "vi")
    assert "only greets" in seen[0] and '"Chào các bạn, hôm nay' in seen[0]
    # A framing sentence that also states something is still judged on what it states.
    assert "judged on that" in seen[0]


def test_the_card_judge_has_no_framing_rule(monkeypatch):
    # A card has no greeting: a card front that "announces" a topic is a question to be answered.
    seen = []
    monkeypatch.setattr(claim_evaluation, "complete_structured", lambda prompt, response_model, **kw: (
        seen.append(kw["system_instruction"]), SupportVerdict(supported=True, reason=""))[1])
    fact_check.check_against_sources([("claim", ["passage"])], "gemini-flash", "vi")
    assert "only greets" not in seen[0]


def test_a_vietnamese_lesson_asks_the_judge_to_name_the_evidence_in_vietnamese(monkeypatch):
    # The input key is "evidence", and a judge that copies it puts an English word in a flag the
    # lecturer reads.
    seen = {}

    def fake(prompt, response_model, **kwargs):
        seen[kwargs["system_instruction"]] = None
        return SupportVerdict(supported=True, reason="")

    monkeypatch.setattr(claim_evaluation, "complete_structured", fake)
    fact_check.check_against_sources([("claim", ["passage"])], "gemini-flash", "vi")
    fact_check.check_against_sources([("claim", ["passage"])], "gemini-flash", "en")

    vi, en = seen
    assert "đoạn nguồn" in vi and "đoạn nguồn" not in en


def test_the_judge_is_pinned_so_an_outage_cannot_fall_back_to_the_generators_family(monkeypatch):
    """Every judge alias falls back to gemini-flash in the gateway: unpinned, Gemini would check its own cards.
    Pinned, a judge outage is an error, and the statement is flagged for the lecturer instead of approved."""
    from types import SimpleNamespace

    from app.llm import gateway

    seen = []

    def fake_completion(**kwargs):
        seen.append(kwargs.get("extra_body"))
        if (kwargs.get("extra_body") or {}).get("disable_fallbacks"):
            raise RuntimeError("claude-fast unavailable")
        # Unpinned, the gateway would have served this from gemini-flash and approved it.
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"supported": true, "reason": "", "verdicts": [{"number": 1, "supported": true, "reason": ""}]}'))])

    monkeypatch.setattr(gateway, "_completion", fake_completion)
    single = fact_check.check_against_sources([("claim", ["passage"])], "gemini-flash", "vi")
    items = fact_check.check_items_against_sources([([("Slide", "bullet")], ["passage"])], "gemini-flash", "vi")
    assert single[0].startswith("Chưa kiểm được: ")
    assert items[0][0].startswith("Slide: Chưa kiểm được: ")
    assert len(seen) == 2 and all(body == {"disable_fallbacks": True} for body in seen)


@pytest.mark.parametrize("alias", ["gemini-flash", "claude-fast", "openai-fast", "deepseek-flash", "qwen-fast"])
def test_the_fact_check_and_the_study_judge_read_a_family_from_the_same_table(alias):
    """Two tables drifted: a new `qwen-fast` was known to the study judge and refused by the fact check."""
    from app.llm.judge import model_family

    assert fact_check.alias_family(alias) == model_family(alias)
