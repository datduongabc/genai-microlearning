"""Runtime-role requests and real worker transactions with a fake gateway completion."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
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


def start(world, sid):
    return client.post(f"/studies/{sid}/judge", headers=world["headers"])


def run(jid, steps):
    for _ in range(steps):
        clear_reservation(jid)
        step(jid)


def judge_rows(sid):
    with SessionLocal() as db:
        return {r.study_item_id: r for r in db.scalars(
            select(Rating).join(StudyItem).where(StudyItem.study_id == uuid.UUID(sid)))}


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


def test_oversized_packet_fails_at_once_without_calling_or_reserving_groq(world, fake, monkeypatch):
    monkeypatch.setattr("random.SystemRandom.shuffle", lambda self, items: None)
    sid = create_study(world)
    big = human_packet()
    big["content_markdown"] = "Cookie là một mẩu dữ liệu nhỏ mà máy chủ gửi cho trình duyệt. " * 5000
    load(world, sid, human_packets=[big, human_packet()])
    jid = start(world, sid).json()["job_id"]
    step(jid)
    result = status(world, sid, jid)
    assert result["completed"] == 1 and result["results"][0]["status"] == "failed"
    assert result["results"][0]["error"].startswith("Packet too large")
    assert fake == []
    with SessionLocal() as db:
        job = db.get(Job, uuid.UUID(jid))
        assert job.attempts == 0 and "groq_reserved_until" not in job.payload
    step(jid)
    result = status(world, sid, jid)
    assert result["status"] == "done" and result["results"][1]["status"] == "done"



def test_whole_large_packet_is_judged_once_without_splitting_or_truncation(world, fake):
    from app.generation.evaluation import _SYSTEM_INSTRUCTION, ModuleContentInput, _build_prompt

    packet = human_packet()
    packet["source_chunks"] = ["Heap source reference " * 2200 + "END OF FULL SOURCE"]
    packet["content_markdown"] = "Full module lesson " * 1200 + "END OF FULL CONTENT"
    model = ModuleContentInput.model_validate(packet)
    tokens = litellm.token_counter(messages=[
        {"role": "system", "content": _SYSTEM_INSTRUCTION},
        {"role": "user", "content": _build_prompt(model)},
    ])
    assert 8000 < tokens < 128000
    sid = create_study(world)
    items = load(world, sid, human_packets=[packet]).json()
    jid = start(world, sid).json()["job_id"]
    step(jid)
    assert status(world, sid, jid)["results"][0]["status"] == "done"
    assert len(fake) == 1
    delivered = fake[0]["messages"][1]["content"]
    model.anon_id = items[0]["anon_code"]
    assert delivered == _build_prompt(model)
    assert "END OF FULL SOURCE" in delivered and "END OF FULL CONTENT" in delivered


def test_worker_cannot_write_human_ratings_or_change_rating_identity(world, fake):
    from sqlalchemy.exc import IntegrityError, ProgrammingError

    sid = create_study(world)
    items = load(world, sid, human_packets=[human_packet(), human_packet()]).json()
    run(start(world, sid).json()["job_id"], 2)
    values = _evaluation(items[0]["anon_code"]).model_dump(exclude={"anon_id"})
    with WorkerSessionLocal() as db:
        db.add(Rating(study_item_id=uuid.UUID(items[0]["id"]), user_id=world["teacher"],
                      rater_kind="human", **values))
        with pytest.raises(ProgrammingError, match="Study not found"):
            db.commit()
        db.rollback()
        row = db.scalar(select(Rating).where(Rating.study_item_id == uuid.UUID(items[0]["id"])))
        row.study_item_id = uuid.UUID(items[1]["id"])
        with pytest.raises(IntegrityError, match="Rating identity cannot change"):
            db.commit()
        db.rollback()


def test_gateway_context_window_rejection_fails_the_item_without_retrying(world, fake, monkeypatch):
    sid = create_study(world)
    load(world, sid, human_packets=[human_packet()])
    jid = start(world, sid).json()["job_id"]
    calls = []

    def rejected(*args, **kwargs):
        calls.append(1)
        raise litellm.BadRequestError(
            "OpenAIException - litellm.ContextWindowExceededError: litellm._pre_call_checks: "
            "Context Window exceeded for given call.", model="groq-pro", llm_provider="openai")

    monkeypatch.setattr(judge, "judge_module", rejected)
    step(jid)
    result = status(world, sid, jid)
    assert len(calls) == 1 and result["status"] == "done"
    assert result["results"][0]["status"] == "failed"
    assert "ContextWindowExceededError" in result["results"][0]["error"]


def test_same_family_refusal_includes_actual_control_model(world, fake):
    sid = create_study(world)
    load(world, sid, module_ids=[str(world["rag"])])
    with SessionLocal() as db:
        item = db.scalar(select(StudyItem).where(StudyItem.study_id == uuid.UUID(sid), StudyItem.group == "llm_no_rag"))
        item.generation_models = [{"served_model": "openai/gpt-4o-mini"}]
        db.commit()
    assert start(world, sid).status_code == 422
    assert fake == []


def test_human_packets_load_after_a_judge_run_and_the_rerun_grades_them(world, fake):
    sid = create_study(world)
    first = load(world, sid, module_ids=[str(world["rag"])]).json()
    run(start(world, sid).json()["job_id"], 2)
    before = judge_rows(sid)
    assert len(before) == 2
    with SessionLocal() as db:
        db.query(Job).filter(Job.study_id == uuid.UUID(sid)).delete()
        db.commit()
    # Judge ratings do not freeze the study; the whole draft is still re-blinded on load.
    response = load(world, sid, human_packets=[human_packet()])
    assert response.status_code == 201, response.text
    items = response.json()
    assert sorted(i["anon_code"] for i in items) == ["M01", "M02", "M03"]
    assert all(i["packet"]["anon_id"] == i["anon_code"] for i in items)
    assert {i["id"] for i in first} < {i["id"] for i in items}
    jid = start(world, sid).json()["job_id"]
    run(jid, 3)
    assert [r["status"] for r in status(world, sid, jid)["results"]] == ["done"] * 3
    after = judge_rows(sid)
    assert len(after) == 3
    # Existing items keep their one row (upsert); the prompt carried each item's new code.
    assert {item: row.id for item, row in before.items()}.items() <= {item: row.id for item, row in after.items()}.items()
    sent_codes = [r["messages"][1]["content"].split("anon_id=")[1].split(")")[0] for r in fake[-3:]]
    assert sorted(sent_codes) == ["M01", "M02", "M03"]


def test_default_model_items_without_recorded_provenance_are_still_checked(world, fake, monkeypatch):
    sid = create_study(world)
    load(world, sid, module_ids=[str(world["rag"])])
    with SessionLocal() as db:
        for item in db.scalars(select(StudyItem).where(StudyItem.study_id == uuid.UUID(sid))):
            item.generator_model = None
            item.generation_models = None
        db.commit()
    # Legacy items fall back to the configured default generator instead of blocking the study.
    assert start(world, sid).status_code == 202
    monkeypatch.setattr(judge.gateway.settings, "llm_model", "openai-fast")
    sid = create_study(world)
    load(world, sid, human_packets=[human_packet()])
    with SessionLocal() as db:
        item = db.scalar(select(StudyItem).where(StudyItem.study_id == uuid.UUID(sid)))
        item.group = "llm_no_rag"
        db.commit()
    assert start(world, sid).status_code == 422


def test_unreachable_gateway_is_503_not_500(world, fake, monkeypatch):
    sid = create_study(world)
    load(world, sid, human_packets=[human_packet()])

    def down():
        raise httpx.ConnectError("gateway down")

    monkeypatch.setattr(judge, "fetch_model_info", down)
    assert start(world, sid).status_code == 503


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
