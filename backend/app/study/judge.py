"""One packet per durable queue step; retries never repeat already completed items."""
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import litellm
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import WorkerSessionLocal
from app.generation.evaluation import (
    _SYSTEM_INSTRUCTION,
    ModuleContentInput,
    ModuleEvaluation,
    _build_prompt,
    judge_module,
)
from app.jobs.queue import is_terminal_error, jittered_backoff
from app.llm import gateway
from app.llm.catalog import GatewayUnavailable, fetch_model_info
from app.llm.judge import refuse_same_family
from app.models import Job, JobStatus, Rating, StudyItem

# Decided for the study: a different family from the Gemini models that generate the modules.
JUDGE_ALIAS = "groq-pro"
OUTPUT_TOKENS = 2048


def validate_judge(items: list[StudyItem]) -> tuple[str, int | None]:
    """The judge's real model and the gateway's input cap for it; ValueError if the judge
    shares a family with any item's generator."""
    try:
        model_info = fetch_model_info()
    except httpx.HTTPError as exc:
        raise GatewayUnavailable(str(exc)) from exc
    model = refuse_same_family(JUDGE_ALIAS, [
        # Items loaded before provenance was stored: assume the configured default generator.
        (item.anon_code, item.generator_model or gateway.active_model(), item.generation_models or [])
        for item in items if item.group != "human"
    ], model_info)
    declared = next(m for m in model_info if m["model_name"] == JUDGE_ALIAS).get("model_info") or {}
    return model, declared.get("max_input_tokens")


def check_packet_budget(packet: ModuleContentInput, max_input_tokens: int | None) -> None:
    if max_input_tokens is None:
        return
    messages = [{"role": "system", "content": _SYSTEM_INSTRUCTION},
                {"role": "user", "content": _build_prompt(packet)}]
    # No model argument: the gateway's pre-call check counts with litellm's default tokenizer.
    tokens = litellm.token_counter(messages=messages)
    if tokens > max_input_tokens:
        raise ValueError(f"Packet too large for the judge model: {tokens} input tokens exceed the "
                         f"gateway's limit of {max_input_tokens}; packet was not truncated")


def is_context_window_error(exc: Exception) -> bool:
    # The gateway's own rejection reaches the client as a plain 400 that only names the error.
    return isinstance(exc, litellm.BadRequestError) and "ContextWindowExceededError" in str(exc)


def upsert_rating(db: Session, item: StudyItem, alias: str, served: str,
                  evaluation: ModuleEvaluation) -> None:
    if evaluation.anon_id != item.anon_code:
        raise ValueError("Judge returned a different anonymous item code")
    values = evaluation.model_dump(exclude={"anon_id"})
    statement = insert(Rating).values(study_item_id=item.id, rater_kind="llm_judge",
                                     judge_alias=alias, judge_model=served, **values)
    db.execute(statement.on_conflict_do_update(
        index_elements=[Rating.study_item_id],
        index_where=Rating.rater_kind == "llm_judge",
        set_={**values, "judge_alias": alias, "judge_model": served, "created_at": func.now()},
    ))


def _requeue(job: Job, when: datetime) -> None:
    job.status = JobStatus.queued
    job.run_after = when
    job.locked_at = None
    job.locked_by = None


def run_study_judge_step(queued: Job) -> None:
    with WorkerSessionLocal() as db:
        job = db.get(Job, queued.id)
        payload = job.payload
        cursor = payload.get("cursor", 0)
        item = db.get(StudyItem, uuid.UUID(payload["item_ids"][cursor]))
        alias = payload["judge_alias"]
        expected = payload["judge_model"]
        try:
            if item is None or item.study_id != job.study_id:
                raise ValueError("Study item no longer exists")
            packet = ModuleContentInput.model_validate(item.packet)
            check_packet_budget(packet, payload["max_input_tokens"])
            with gateway.experiment_mode(), gateway.using_model(alias), gateway.capture_models() as calls:
                evaluation = judge_module(packet, max_tokens=OUTPUT_TOKENS, max_retries=0, timeout=120)
            if not calls or any(c["served_model"].split("/")[-1] != expected.split("/")[-1] for c in calls):
                raise ValueError("Gateway did not report the pinned judge model; rating was not saved")
            served = calls[-1]["served_model"]
            upsert_rating(db, item, alias, served, evaluation)
            result = {"item_id": str(item.id), "status": "done", "judge_model": served}
        except Exception as exc:
            db.rollback()
            job = db.get(Job, queued.id)
            message = str(exc)[:1000]
            terminal = is_terminal_error(exc) or is_context_window_error(exc)
            if not terminal and job.attempts + 1 < job.max_attempts:
                job.attempts += 1
                job.last_error = message
                _requeue(job, datetime.now(UTC) + timedelta(seconds=jittered_backoff(job.attempts)))
                db.commit()
                return
            result = {"item_id": payload["item_ids"][cursor], "status": "failed", "error": message}
        job.payload = {**job.payload, "cursor": cursor + 1,
                       "results": [*job.payload.get("results", []), result]}
        job.attempts = 0
        job.last_error = None
        if cursor + 1 == len(payload["item_ids"]):
            job.status = JobStatus.done
            job.locked_at = None
            job.locked_by = None
        else:
            _requeue(job, datetime.now(UTC))
        db.commit()
