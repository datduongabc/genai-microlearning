"""A second model reads each generated statement against the passages it cites.

The model that wrote a statement tends to agree with it, so the check runs on a model of another
family. A statement the judge does not find in its passages is flagged with the judge's reason,
never dropped: the lecturer decides what to do with it."""

import logging

from app.generation.claim_evaluation import _support
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
