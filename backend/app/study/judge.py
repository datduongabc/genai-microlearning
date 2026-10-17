"""One packet per durable queue step; retries never repeat already completed items."""
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import litellm
from sqlalchemy import func, select, text
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
from app.llm.judge import eligible_judges, is_quota_error, model_family
from app.models import Job, JobStatus, Rating, StudyItem

OUTPUT_TOKENS = 2048


def item_generators(item: StudyItem) -> list:
    return [] if item.group == "human" else [
        (item.anon_code, item.generator_model or gateway.active_model(), item.generation_models or [])]


def validate_judge(items: list[StudyItem]) -> list[dict]:
    try:
        model_info = fetch_model_info()
    except httpx.HTTPError as exc:
        raise GatewayUnavailable(str(exc)) from exc
    for item in items:
        candidates = eligible_judges(item_generators(item), model_info)
        if len({model_family(c["model"]) for c in candidates}) < 2:
            raise ValueError(f"{item.anon_code}: thiếu hai hãng chấm khác hãng sinh; chưa chấm")
    # Persist only routing/provenance fields, never provider credentials from /model/info.
    return [{"model_name": m["model_name"],
             "litellm_params": {"model": m.get("litellm_params", {}).get("model", "")},
             "model_info": {"max_input_tokens": (m.get("model_info") or {}).get("max_input_tokens")}}
            for m in model_info]


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
                                     judge_alias=alias, judge_model=served, judge_family=model_family(served), **values)
    db.execute(statement.on_conflict_do_update(
        index_elements=[Rating.study_item_id, Rating.judge_family],
        # A bound predicate stops matching the partial index once PostgreSQL uses a generic plan.
        index_where=text("rater_kind = 'llm_judge'"),
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
        alias = None
        try:
            if item is None or item.study_id != job.study_id:
                raise ValueError("Study item no longer exists")
            packet = ModuleContentInput.model_validate(item.packet)
            existing = list(db.scalars(select(Rating).where(
                Rating.study_item_id == item.id, Rating.rater_kind == "llm_judge")))
            # Only count prior ratings still valid against the full generation provenance.
            catalog = payload.get("model_info") or validate_judge([item])
            allowed = {model_family(c["model"]) for c in eligible_judges(item_generators(item), catalog)}
            rated = [r.judge_model for r in existing if r.judge_model and model_family(r.judge_model) in allowed]
            errors = []
            for candidate in eligible_judges(item_generators(item), catalog, rated):
                if len({model_family(m) for m in rated}) >= 2:
                    break
                alias, expected = candidate["alias"], candidate["model"]
                if model_family(expected) in {model_family(m) for m in rated}:
                    continue
                check_packet_budget(packet, candidate["max_input_tokens"])
                try:
                    with gateway.experiment_mode(), gateway.using_model(alias), gateway.capture_models() as calls:
                        evaluation = judge_module(packet, max_tokens=OUTPUT_TOKENS, max_retries=0, timeout=120)
                except Exception as exc:
                    if not is_quota_error(exc):
                        raise
                    errors.append(f"{alias}: {str(exc)[:200]}")
                    continue
                if len(calls) != 1 or calls[0]["served_model"].split("/")[-1] != expected.split("/")[-1]:
                    raise ValueError("Gateway did not report the pinned judge model; rating was not saved")
                served = calls[0]["served_model"]
                upsert_rating(db, item, alias, served, evaluation)
                db.commit()  # Preserve the first rating when the second hits quota or the worker stops.
                rated.append(served)
            missing = max(0, 2 - len({model_family(m) for m in rated}))
            if missing:
                job.last_error = f"{item.anon_code}: missing {missing}/2 ratings; " + "; ".join(errors)
                job.status = JobStatus.failed  # A new study judge job resumes from saved ratings.
                job.locked_at = None
                job.locked_by = None
                db.commit()
                return
            result = {"item_id": str(item.id), "status": "done", "judge_models": rated}
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
            job.status = JobStatus.failed
            job.last_error = f"{item.anon_code if item else payload['item_ids'][cursor]}: rating pending ({alias}): {message}"
            job.locked_at = None
            job.locked_by = None
            db.commit()
            return
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
