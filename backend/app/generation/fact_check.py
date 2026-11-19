"""A second model reads each generated statement against the passages it cites.

The model that wrote a statement tends to agree with it, so the check runs on a model of another
family. A statement the judge does not find in its passages is flagged with the judge's reason,
never dropped: the lecturer decides what to do with it."""

import json
import logging

from pydantic import BaseModel

from app.generation import claim_evaluation
from app.generation.claim_evaluation import _SUPPORT_INSTRUCTION, SupportVerdict
from app.generation.common import OutputLanguage
from app.llm.gateway import using_model
from app.llm.judge import known_family

logger = logging.getLogger(__name__)


# Cheap judges first: a set is checked card by card. The first one of another family is used.
_JUDGES = ("claude-fast", "openai-fast")
# Every judge alias falls back to gemini-flash in the gateway, so an unpinned judge call could be served by the
# generator's own family. Pinned, an outage raises and the statement is flagged "Chưa kiểm được" instead.
_PINNED = {"disable_fallbacks": True}


def alias_family(alias: str) -> str:
    # The study judge's table, so a new model is known to both checks or to neither.
    family = known_family(alias)
    if family is None:
        # Guessing would let a model check its own work without anyone knowing.
        raise ValueError(f"Cannot tell which model family the alias '{alias}' belongs to")
    return family


def judge_alias(generator_alias: str) -> str:
    generator_family = alias_family(generator_alias)
    return next(j for j in _JUDGES if alias_family(j) != generator_family)


_LANGUAGE_NAMES = {"vi": "Vietnamese", "en": "English"}


# The input key is "evidence", which a judge copies into its reason as an English word.
_EVIDENCE_NAME = {"vi": ' Call the evidence "đoạn nguồn" in it, never "evidence".'}


def _with_language(criteria: str, language: OutputLanguage) -> str:
    """The judge's criteria, then what a lesson in `language` adds to them. The documents are often
    English and the lesson Vietnamese, so a claim is a translation of its evidence: the live run's
    judge, reading wording, took "thời điểm số không" (time zero) for "not in that order". Its
    reason is read by the lecturer, in the editor of a lesson in that language."""
    return criteria + f"""
The claim may be in another language than the evidence: it is often a translation or a paraphrase of it,
for example a Vietnamese sentence about an English slide. Judge the meaning, not the wording: a faithful
translation is supported. Numbers may be spelled out as spoken words: "số không" is 0,
"hai mươi bốn" is 24, "one over n" is 1/n.
Write `reason` in {_LANGUAGE_NAMES[language]}.{_EVIDENCE_NAME.get(language, "")}"""


def _support(claim: str, passages: list[str], language: OutputLanguage) -> SupportVerdict:
    return claim_evaluation.complete_structured(
        json.dumps({"claim": claim, "evidence": passages}, ensure_ascii=False),
        SupportVerdict, system_instruction=_with_language(_SUPPORT_INSTRUCTION, language), temperature=0,
        extra_body=_PINNED,
    )


# The flags that are not a verdict: nothing to check against, or a judge that could not answer.
_NO_PASSAGE = "Chưa có đoạn nguồn để kiểm"
_JUDGE_FAILED = "Chưa kiểm được"


def _report(kind: str, flags: list[str | None]) -> None:
    """One line per check, which CloudWatch counts (infra/terraform/monitoring.tf). `judge_failed`
    rising means the judge is down or out of credit, and drafts reach lecturers unchecked; an item
    with no passage is the draft's own state, so it is counted apart and raises no alarm."""
    reasons = [f.split(": ", 1)[1] if kind == "items" and ": " in f else f for f in flags if f is not None]
    no_passage = sum(1 for r in reasons if r.startswith(_NO_PASSAGE))
    judge_failed = sum(1 for r in reasons if r.startswith(_JUDGE_FAILED))
    logger.info("ai_quality fact_check", extra={
        "kind": kind, "checked": len(flags) - no_passage - judge_failed,
        "unsupported": len(reasons) - no_passage - judge_failed, "no_passage": no_passage, "judge_failed": judge_failed,
    })


def check_against_sources(
    statements: list[tuple[str, list[str]]], generator_alias: str, language: OutputLanguage,
) -> list[str | None]:
    """For each (statement, passages): None when the passages support it, otherwise why not."""
    judge = judge_alias(generator_alias)
    flags: list[str | None] = []
    with using_model(judge):
        for statement, passages in statements:
            if not passages:
                flags.append(_NO_PASSAGE)
                continue
            try:
                verdict = _support(statement, passages, language)
            except Exception as err:
                # The check flags, it never blocks: an outage must not cost the lecturer the cards.
                logger.warning("fact check could not run: %s", err)
                flags.append(f"{_JUDGE_FAILED}: {str(err)[:120]}")
                continue
            flags.append(None if verdict.supported else (verdict.reason or "Không được đoạn nguồn xác nhận"))
    _report("statements", flags)
    return flags


class ItemVerdict(BaseModel):
    number: int
    supported: bool
    reason: str


class ItemVerdicts(BaseModel):
    verdicts: list[ItemVerdict]


# The single-claim criteria, unchanged, so the two checks cannot drift apart; only how the answer
# is laid out differs.
_ITEMS_INSTRUCTION = _SUPPORT_INSTRUCTION + """
The claims are numbered. Judge each claim on its own against the whole evidence, by the criteria
above, and return one verdict for every number."""


# Items are a video scene's slide and narration, and narration greets the viewer and says what comes
# next: the script prompt asks for that. Live run 2's judge flagged scene 1's greeting and plan as
# unsupported, so every video began with a scene to review. Told to the judge rather than skipping the
# title scene, whose other sentences state facts that must still be checked.
_FRAMING_RULE = """
A sentence that only greets the viewer, says what the lesson or the next scene is about, or links one scene to
the next (for example "Chào các bạn, hôm nay chúng ta tìm hiểu FCFS và Round Robin", "Tiếp theo, hãy xem một
ví dụ") makes no claim about the subject: it is supported. A sentence that also states a fact about the subject
is judged on that fact."""


def _judge_items(claims: list[str], passages: list[str], language: OutputLanguage) -> list[ItemVerdict]:
    reply = claim_evaluation.complete_structured(
        json.dumps({"claims": [{"number": n, "claim": c} for n, c in enumerate(claims, 1)], "evidence": passages},
                   ensure_ascii=False),
        ItemVerdicts, system_instruction=_with_language(_ITEMS_INSTRUCTION + _FRAMING_RULE, language), temperature=0,
        extra_body=_PINNED,
    )
    return reply.verdicts


def check_items_against_sources(
    groups: list[tuple[list[tuple[str, str]], list[str]]], generator_alias: str, language: OutputLanguage,
) -> list[list[str | None]]:
    """For each group of (label, item) sharing passages, such as the bullets and sentences of one
    video scene: None for an item the passages support, otherwise "label: why not".

    One judge call per group, not per item: every call sends the passages again, and a scene has
    a dozen items. The guarantees of check_against_sources hold: another family judges, an unknown
    alias fails before any call, and an outage flags every item of the group instead of raising."""
    judge = judge_alias(generator_alias)
    flags: list[list[str | None]] = []
    with using_model(judge):
        for items, passages in groups:
            if not passages:
                flags.append([f"{label}: {_NO_PASSAGE}" for label, _ in items])
                continue
            try:
                verdicts = _judge_items([text for _, text in items], passages, language)
            except Exception as err:
                # The check flags, it never blocks: an outage must not cost the lecturer the script.
                logger.warning("fact check could not run: %s", err)
                flags.append([f"{label}: {_JUDGE_FAILED}: {str(err)[:120]}" for label, _ in items])
                continue
            numbers = sorted(v.number for v in verdicts)
            if numbers != list(range(1, len(items) + 1)):
                # A repeated, missing or shifted number means no verdict can be matched to its item
                # for sure, and a "supported" given to the wrong item would hide a real error.
                flags.append([f"{label}: {_JUDGE_FAILED}: bước kiểm tra trả lời sai thứ tự" for label, _ in items])
                continue
            by_number = {v.number: v for v in verdicts}
            flags.append([
                None if by_number[n].supported else f"{label}: {by_number[n].reason or 'Không được đoạn nguồn xác nhận'}"
                for n, (label, _) in enumerate(items, 1)])
    _report("items", [flag for group in flags for flag in group])
    return flags
