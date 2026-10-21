"""Process a pair per durable queue step; preserve saved ratings across retries."""
import uuid
from collections.abc import Callable
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


def pair_groups(items: list[StudyItem]) -> list[list[StudyItem]]:
    groups: dict = {}
    for item in items:
        groups.setdefault(item.pair_id or item.id, []).append(item)
    return list(groups.values())


def validate_judge(items: list[StudyItem]) -> list[dict]:
    try:
        model_info = fetch_model_info()
    except httpx.HTTPError as exc:
        raise GatewayUnavailable(str(exc)) from exc
    for members in pair_groups(items):
        candidates = eligible_judges([g for m in members for g in item_generators(m)], model_info)
        if len({model_family(c["model"]) for c in candidates}) < 2:
            codes = ", ".join(m.anon_code for m in members)
            raise ValueError(f"{codes}: thiếu hai hãng chấm khác hãng sinh; chưa chấm")
    # Persist only routing/provenance fields, never provider credentials from /model/info.
    return [{"model_name": m["model_name"],
             "litellm_params": {"model": m.get("litellm_params", {}).get("model", "")},
             "model_info": {"max_input_tokens": (m.get("model_info") or {}).get("max_input_tokens")}}
            for m in model_info]


class PacketTooLarge(ValueError):
    pass


def check_packet_budget(packet: ModuleContentInput, max_input_tokens: int | None) -> None:
    if max_input_tokens is None:
        return
    messages = [{"role": "system", "content": _SYSTEM_INSTRUCTION},
                {"role": "user", "content": _build_prompt(packet)}]
    # No model argument: the gateway's pre-call check counts with litellm's default tokenizer.
    tokens = litellm.token_counter(messages=messages)
    if tokens > max_input_tokens:
        raise PacketTooLarge(f"Packet too large for the judge model: {tokens} input tokens exceed the "
                         f"gateway's limit of {max_input_tokens}; packet was not truncated")


def judge_pair(packets: dict, generators: list, catalog: list[dict], rated: dict[object, set[str]],
               rate: Callable[[object, ModuleContentInput, dict], None]) -> tuple[int, list[str]]:
    """Rate every member of a pair with the same two judge aliases, so paired analysis keeps the pair.

    Returns the number of aliases still missing for the pair and the skipped candidates' errors."""
    candidates = eligible_judges(generators, catalog)
    # Count complete aliases first, then finish started aliases before opening new ones.
    candidates.sort(key=lambda c: (
        not all(c["alias"] in rated[key] for key in packets),
        not any(c["alias"] in rated[key] for key in packets)))
    done, errors = 0, []
    for candidate in candidates:
        if done >= 2:
            break
        alias = candidate["alias"]
        try:
            for packet in packets.values():
                check_packet_budget(packet, candidate["max_input_tokens"])
            for key, packet in packets.items():
                if alias not in rated[key]:
                    rate(key, packet, candidate)
                    rated[key].add(alias)
        except Exception as exc:
            if not (is_quota_error(exc) or is_context_window_error(exc)
                    or isinstance(exc, litellm.APIError) and exc.status_code == 403 or isinstance(
                    exc, PacketTooLarge | litellm.NotFoundError | litellm.PermissionDeniedError)):
                raise
            errors.append(f"{alias}: {str(exc)[:200]}")
            continue
        done += 1
    return max(0, 2 - done), errors


def is_context_window_error(exc: Exception) -> bool:
    # The gateway's own rejection reaches the client as a plain 400 that only names the error.
    return isinstance(exc, litellm.ContextWindowExceededError) or (
        isinstance(exc, litellm.BadRequestError) and "ContextWindowExceededError" in str(exc))


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
        try:
            if item is None or item.study_id != job.study_id:
                raise ValueError("Study item no longer exists")
            members = [item] if item.pair_id is None else list(db.scalars(select(StudyItem).where(
                StudyItem.study_id == item.study_id, StudyItem.pair_id == item.pair_id).order_by(StudyItem.anon_code)))
            by_id = {m.id: m for m in members}
            packets = {m.id: ModuleContentInput.model_validate(m.packet) for m in members}
            rated = {m.id: set() for m in members}
            for r in db.scalars(select(Rating).where(Rating.study_item_id.in_(by_id), Rating.rater_kind == "llm_judge")):
                rated[r.study_item_id].add(r.judge_alias)
            catalog = payload.get("model_info") or validate_judge(members)

            def rate(item_id, packet, candidate):
                alias, expected = candidate["alias"], candidate["model"]
                with gateway.experiment_mode(), gateway.using_model(alias), gateway.capture_models() as calls:
                    evaluation = judge_module(packet, max_tokens=OUTPUT_TOKENS, max_retries=0, timeout=120)
                if len(calls) != 1 or calls[0]["served_model"].split("/")[-1] != expected.split("/")[-1]:
                    raise ValueError("Gateway did not report the pinned judge model; rating was not saved")
                upsert_rating(db, by_id[item_id], alias, calls[0]["served_model"], evaluation)
                db.commit()  # Preserve each rating when a later one hits quota or the worker stops.

            missing, errors = judge_pair(packets, [g for m in members for g in item_generators(m)],
                                         catalog, rated, rate)
            if missing:
                outcomes = {str(m.id): {"item_id": str(m.id), "status": "pending",
                                        "error": f"{m.anon_code}: missing {missing}/2 ratings; " + "; ".join(errors)}
                            for m in members}
            else:
                outcomes = {str(m.id): {"item_id": str(m.id), "status": "done", "judge_models": sorted(db.scalars(
                    select(Rating.judge_model).where(Rating.study_item_id == m.id,
                                                     Rating.rater_kind == "llm_judge")))} for m in members}
            result = outcomes[str(item.id)]
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
            result = {"item_id": payload["item_ids"][cursor], "status": "pending", "error": message}
            outcomes = {}
        results = [outcomes.get(r["item_id"], r) for r in job.payload.get("results", [])]
        job.payload = {**job.payload, "cursor": cursor + 1, "results": [*results, result]}
        job.attempts = 0
        job.last_error = result.get("error")
        if cursor + 1 == len(payload["item_ids"]):
            job.status = JobStatus.done
            job.locked_at = None
            job.locked_by = None
        else:
            _requeue(job, datetime.now(UTC))
        db.commit()
