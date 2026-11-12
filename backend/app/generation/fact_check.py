"""A second model reads each generated statement against the passages it cites.

The model that wrote a statement tends to agree with it, so the check runs on a model of another
family. A statement the judge does not find in its passages is flagged with the judge's reason,
never dropped: the lecturer decides what to do with it."""

import json
import logging

from pydantic import BaseModel

from app.generation import claim_evaluation
from app.generation.claim_evaluation import _SUPPORT_INSTRUCTION, _support
from app.llm.gateway import using_model

logger = logging.getLogger(__name__)

# Family of a gateway alias, by its prefix (devops/gateway/config.yaml). Groq serves other makers'
# models, so its aliases are named one by one after the model behind them.
_FAMILY_BY_ALIAS = {
    "groq-pro": "openai",  # gpt-oss
    "groq-llama": "meta",
    "groq-qwen": "alibaba",
    "groq-minimax": "minimax",
}
_FAMILY_BY_PREFIX = {"gemini": "google", "claude": "anthropic", "openai": "openai"}

# Cheap judges first: a set is checked card by card. The first one of another family is used.
_JUDGES = ("claude-fast", "openai-fast")


def alias_family(alias: str) -> str:
    if alias in _FAMILY_BY_ALIAS:
        return _FAMILY_BY_ALIAS[alias]
    family = _FAMILY_BY_PREFIX.get(alias.split("-")[0])
    if family is None:
        # Guessing would let a model check its own work without anyone knowing.
        raise ValueError(f"Cannot tell which model family the alias '{alias}' belongs to")
    return family


def judge_alias(generator_alias: str) -> str:
    generator_family = alias_family(generator_alias)
    return next(j for j in _JUDGES if alias_family(j) != generator_family)


def check_against_sources(statements: list[tuple[str, list[str]]], generator_alias: str) -> list[str | None]:
    """For each (statement, passages): None when the passages support it, otherwise why not."""
    judge = judge_alias(generator_alias)
    flags: list[str | None] = []
    with using_model(judge):
        for statement, passages in statements:
            if not passages:
                flags.append("Chưa có đoạn nguồn để kiểm")
                continue
            try:
                verdict = _support(statement, passages, {})
            except Exception as err:
                # The check flags, it never blocks: an outage must not cost the lecturer the cards.
                logger.warning("fact check could not run: %s", err)
                flags.append(f"Chưa kiểm được: {str(err)[:120]}")
                continue
            flags.append(None if verdict.supported else (verdict.reason or "Không được đoạn nguồn xác nhận"))
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


def _judge_items(claims: list[str], passages: list[str]) -> list[ItemVerdict]:
    reply = claim_evaluation.complete_structured(
        json.dumps({"claims": [{"number": n, "claim": c} for n, c in enumerate(claims, 1)], "evidence": passages},
                   ensure_ascii=False),
        ItemVerdicts, system_instruction=_ITEMS_INSTRUCTION, temperature=0,
    )
    return reply.verdicts


def check_items_against_sources(
    groups: list[tuple[list[tuple[str, str]], list[str]]], generator_alias: str,
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
                flags.append([f"{label}: Chưa có đoạn nguồn để kiểm" for label, _ in items])
                continue
            try:
                verdicts = _judge_items([text for _, text in items], passages)
            except Exception as err:
                # The check flags, it never blocks: an outage must not cost the lecturer the script.
                logger.warning("fact check could not run: %s", err)
                flags.append([f"{label}: Chưa kiểm được: {str(err)[:120]}" for label, _ in items])
                continue
            numbers = sorted(v.number for v in verdicts)
            if numbers != list(range(1, len(items) + 1)):
                # A repeated, missing or shifted number means no verdict can be matched to its item
                # for sure, and a "supported" given to the wrong item would hide a real error.
                flags.append([f"{label}: Chưa kiểm được: model kiểm trả lời sai số thứ tự" for label, _ in items])
                continue
            by_number = {v.number: v for v in verdicts}
            flags.append([
                None if by_number[n].supported else f"{label}: {by_number[n].reason or 'Không được đoạn nguồn xác nhận'}"
                for n, (label, _) in enumerate(items, 1)])
    return flags
