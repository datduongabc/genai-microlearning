"""One packet per durable queue step; retries never repeat already completed items."""
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import litellm
from sqlalchemy import Float, cast, func, select, text
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
from app.models import Job, JobStatus, JobType, Rating, StudyItem

# Decided for the study: a different family from the Gemini models that generate the modules.
JUDGE_ALIAS = "groq-pro"
OUTPUT_TOKENS = 2048
# Reserve 10% against tokenizer/provider differences within Groq's 8,000 TPM limit.
GROQ_TOKEN_BUDGET = 7200
GROQ_INTERVAL_SECONDS = 61


def validate_judge(items: list[StudyItem]) -> str:
    """The judge's real model; ValueError if it shares a family with any item's generator."""
    try:
        model_info = fetch_model_info()
    except httpx.HTTPError as exc:
        raise GatewayUnavailable(str(exc)) from exc
    return refuse_same_family(JUDGE_ALIAS, [
        # Items loaded before provenance was stored: assume the configured default generator.
        (item.anon_code, item.generator_model or gateway.active_model(), item.generation_models or [])
        for item in items if item.group != "human"
    ], model_info)


def check_packet_budget(packet: ModuleContentInput, judge_model: str) -> None:
    if not judge_model.startswith("groq/"):
        return
    messages = [{"role": "system", "content": _SYSTEM_INSTRUCTION},
                {"role": "user", "content": _build_prompt(packet)}]
    tokens = litellm.token_counter(model="gpt-4o", messages=messages)
    tokens += litellm.token_counter(model="gpt-4o", text=json.dumps(ModuleEvaluation.model_json_schema()))
    if tokens + OUTPUT_TOKENS > GROQ_TOKEN_BUDGET:
        raise ValueError(f"Packet too large for Groq's 8,000 TPM: estimated {tokens} input/schema "
                         f"+ {OUTPUT_TOKENS} reserved output tokens (safe budget {GROQ_TOKEN_BUDGET}); "
                         "packet was not truncated")


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


def reserve_groq_call(db: Session, job: Job, now: datetime) -> bool:
    # Serialize reservations across all worker threads/processes, including after restart.
    db.execute(text("SELECT pg_advisory_xact_lock(181903)"))
    reserved_until = db.scalar(select(func.max(cast(Job.payload["groq_reserved_until"].astext, Float)))
                               .where(Job.job_type == JobType.study_judge))
    if reserved_until and now.timestamp() < reserved_until:
        _requeue(job, datetime.fromtimestamp(reserved_until, UTC))
        db.commit()
        return False
    job.payload = {**job.payload, "groq_reserved_until": now.timestamp() + GROQ_INTERVAL_SECONDS}
    db.commit()
    return True


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
            check_packet_budget(packet, expected)
            if expected.startswith("groq/") and not reserve_groq_call(db, job, datetime.now(UTC)):
                return
            with gateway.experiment_mode(), gateway.using_model(alias), gateway.capture_models() as calls:
                evaluation = judge_module(packet, max_tokens=OUTPUT_TOKENS, max_retries=0, timeout=120)
            if len(calls) != 1 or calls[0]["served_model"].split("/")[-1] != expected.split("/")[-1]:
                raise ValueError("Gateway did not report the pinned judge model; rating was not saved")
            served = calls[0]["served_model"]
            upsert_rating(db, item, alias, served, evaluation)
            result = {"item_id": str(item.id), "status": "done", "judge_model": served}
        except Exception as exc:
            db.rollback()
            job = db.get(Job, queued.id)
            message = str(exc)[:1000]
            if not is_terminal_error(exc) and job.attempts + 1 < job.max_attempts:
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
