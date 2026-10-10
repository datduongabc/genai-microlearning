"""Runtime-role requests and real worker transactions with a fake gateway completion."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import litellm
import pytest
from sqlalchemy import select

from app.db import SessionLocal, WorkerSessionLocal
from app.jobs.queue import execute_job_step
from app.models import Job, JobStatus, Rating, StudyItem
from app.study import judge
from tests.integration import test_studies as study_tests
from tests.integration.judge_helpers import GATEWAY, _evaluation
from tests.integration.test_studies import (
    client,
    create_study,
    human_packet,
    load,
)

world = study_tests.world


@pytest.fixture
def fake(monkeypatch):
    sent = []
    monkeypatch.setattr(judge, "fetch_model_info", lambda: GATEWAY + [
        {"model_name": "test-model", "litellm_params": {"model": "gemini/gemini-test"}},
    ])

    def complete(**kwargs):
        sent.append(kwargs)
        # Exercise the real rubric JSON parser and gateway served-model capture.
        code = kwargs["messages"][1]["content"].split("anon_id=")[1].split(")")[0]
        return SimpleNamespace(model="gpt-oss-120b", choices=[SimpleNamespace(
            message=SimpleNamespace(content=_evaluation(code).model_dump_json()))])

    monkeypatch.setattr(litellm, "completion", complete)
    return sent


def start(world, sid, alias="groq-pro"):
    return client.post(f"/studies/{sid}/judge", headers=world["headers"], json={"model_alias": alias})


def step(jid):
    with SessionLocal() as db:
        job = db.get(Job, uuid.UUID(jid))
        job.status = JobStatus.running
        db.commit()
        db.refresh(job)
        db.expunge(job)
    execute_job_step(job)


def status(world, sid, jid):
    return client.get(f"/studies/{sid}/judge/{jid}", headers=world["headers"]).json()


def clear_reservation(jid):
    with SessionLocal() as db:
        job = db.get(Job, uuid.UUID(jid))
        job.payload = {**job.payload, "groq_reserved_until": 0}
        db.commit()


def test_enqueue_parse_all_scores_pin_served_model_and_upsert(world, fake):
    sid = create_study(world)
    items = load(world, sid, module_ids=[str(world["rag"])]).json()
    response = start(world, sid)
    assert response.status_code == 202, response.text
    jid = response.json()["job_id"]
    assert fake == []
    assert start(world, sid).status_code == 409
    assert load(world, sid, human_packets=[human_packet()]).status_code == 409
    step(jid)
    clear_reservation(jid)
    step(jid)
    result = status(world, sid, jid)
    assert result["status"] == "done" and result["completed"] == 2
    assert all(r["status"] == "done" for r in result["results"])
    assert len(fake) == 2
    for request in fake:
        assert request["model"] == "openai/groq-pro"
        assert request["extra_body"]["disable_fallbacks"] is True
        assert request["max_tokens"] == judge.OUTPUT_TOKENS
        assert request["max_retries"] == 0
    with SessionLocal() as db:
        rows = db.scalars(select(Rating).where(Rating.study_item_id.in_([uuid.UUID(i["id"]) for i in items]))).all()
        row_ids = {r.id for r in rows}
        assert len(rows) == 2
        for row in rows:
            assert row.rater_kind == "llm_judge" and row.user_id is None
            assert row.judge_alias == "groq-pro" and row.judge_model == "gpt-oss-120b"
            assert row.accuracy == {"score": 4, "justification": "ổn"}
            assert row.hallucination_findings == []
        db.delete(db.get(Job, uuid.UUID(jid)))
        db.commit()
    second = start(world, sid).json()["job_id"]
    step(second)
    clear_reservation(second)
    step(second)
    with SessionLocal() as db:
        assert {r.id for r in db.scalars(select(Rating))} == row_ids


def test_per_item_error_does_not_abort_later_items(world, fake, monkeypatch):
    sid = create_study(world)
    load(world, sid, human_packets=[human_packet(), human_packet()])
    jid = start(world, sid).json()["job_id"]
    real = judge.judge_module
    calls = []

    def evaluate(packet, **kwargs):
        calls.append(packet.anon_id)
        if len(calls) == 1:
            raise ValueError("invalid rubric response")
        return real(packet, **kwargs)

    monkeypatch.setattr(judge, "judge_module", evaluate)
    step(jid)
    clear_reservation(jid)
    step(jid)
    result = status(world, sid, jid)
    assert result["status"] == "done"
    assert [r["status"] for r in result["results"]] == ["failed", "done"]
    assert result["results"][0]["error"] == "invalid rubric response"


def test_transient_error_retries_same_item_then_records_exhaustion(world, fake, monkeypatch):
    sid = create_study(world)
    load(world, sid, human_packets=[human_packet(), human_packet()])
    jid = start(world, sid).json()["job_id"]

    def quota(*args, **kwargs):
        raise litellm.RateLimitError("quota", llm_provider="groq", model="gpt-oss-120b")

    monkeypatch.setattr(judge, "judge_module", quota)
    with SessionLocal() as db:
        db.get(Job, uuid.UUID(jid)).max_attempts = 2
        db.commit()
    step(jid)
    result = status(world, sid, jid)
    assert result["completed"] == 0 and result["status"] == "queued" and "quota" in result["last_error"]
    with SessionLocal() as db:
        job = db.get(Job, uuid.UUID(jid))
        assert job.attempts == 1 and job.run_after > datetime.now(UTC)
    clear_reservation(jid)
    step(jid)
    result = status(world, sid, jid)
    assert result["completed"] == 1 and result["results"][0]["status"] == "failed"


def test_same_family_refusal_includes_actual_control_model(world, fake):
    sid = create_study(world)
    load(world, sid, module_ids=[str(world["rag"])])
    assert start(world, sid, "gemini-fast").status_code == 422
    with SessionLocal() as db:
        item = db.scalar(select(StudyItem).where(StudyItem.study_id == uuid.UUID(sid), StudyItem.group == "llm_no_rag"))
        item.generation_models = [{"served_model": "openai/gpt-4o-mini"}]
        db.commit()
    assert start(world, sid).status_code == 422
    assert fake == []


@pytest.mark.parametrize("headers_name,code", [(None, 401), ("teacher_headers", 404)])
def test_judge_endpoints_are_admin_only(world, fake, headers_name, code):
    sid = create_study(world)
    headers = world[headers_name] if headers_name else {}
    assert client.post(f"/studies/{sid}/judge", headers=headers, json={}).status_code == code
    assert client.get(f"/studies/{sid}/judge/{uuid.uuid4()}", headers=headers).status_code == code
    assert fake == []


def test_reservation_serializes_concurrent_workers(world, fake):
    jobs = []
    for _ in range(2):
        sid = create_study(world)
        load(world, sid, human_packets=[human_packet()])
        jobs.append(start(world, sid).json()["job_id"])

    def reserve(jid):
        with WorkerSessionLocal() as db:
            return judge.reserve_groq_call(db, db.get(Job, uuid.UUID(jid)), datetime.now(UTC))

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, jobs)) == [False, True]
    with SessionLocal() as db:
        assert any(db.get(Job, uuid.UUID(jid)).run_after > datetime.now(UTC) + timedelta(seconds=50) for jid in jobs)
    assert fake == []
