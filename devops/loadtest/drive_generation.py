"""Seeds users with one course and document each, then queues module generations through the API.

    python drive_generation.py seed --users 40
    python drive_generation.py generate --modules 8 --label workers-1x4
    python drive_generation.py chat --concurrency 25 --messages 50 --label chat-api-1
    python drive_generation.py check --out real-claude-fast.json --token-cap 30000000

`seed` reads and embeds one real lecture once and copies its chunks to every course. `generate` waits
for the jobs, then adds a row with throughput, tokens and per-stage timings to the results file. `chat` sends
real chat messages and records throughput and latency. `check` fails when the last row broke a stop condition."""

import argparse
import asyncio
import concurrent.futures
import json
import re
import sys
import statistics
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import func, select

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.ingestion.chunker import chunk_units, document_title
from app.ingestion.document_readers import read_document
from app.ingestion.embeddings import embed_texts
from app.models import Chunk, Course, Document, DocumentStatus, Job, JobStatus, Module, User

RESULTS = Path("/results")
USERS_FILE = RESULTS / "users.json"
LECTURE = "/documents/operating-system/Chapter 3_Processes.pdf"
BASE = "http://proxy/api"
QUESTIONS = ["Tiến trình là gì?", "Các trạng thái của tiến trình gồm những gì?", "Process Control Block lưu thông tin gì?",
             "Context switch diễn ra thế nào?", "Tiến trình cha và con giao tiếp ra sao?", "Sự khác nhau giữa shared memory và message passing?"]


def seed(count: int) -> None:
    units = read_document(LECTURE, course_id="seed", source_id="lecture.pdf")
    chunks = chunk_units(units, title=document_title("lecture.pdf"))
    vectors = embed_texts([c.content for c in chunks])
    raw_text = "\n\n".join(u.text for u in units if u.text)
    seeded = []
    with SessionLocal() as db:
        for _ in range(count):
            user = User(email=f"load-{uuid.uuid4().hex[:10]}@example.com", name="Load", hashed_password="unused")
            db.add(user)
            db.flush()
            course = Course(name="Load test", owner_id=user.id)
            db.add(course)
            db.flush()
            document = Document(course_id=course.id, filename="lecture.pdf", status=DocumentStatus.embedded,
                                raw_text=raw_text)
            db.add(document)
            db.flush()
            db.add_all(Chunk(document_id=document.id, course_id=course.id, chunk_index=i, content=c.content,
                             source_ref=json.dumps(c.locator, ensure_ascii=False), embedding=v)
                       for i, (c, v) in enumerate(zip(chunks, vectors, strict=True)))
            seeded.append({"user_id": str(user.id), "course_id": str(course.id), "document_id": str(document.id),
                           "token": create_access_token(user.id)})
        db.commit()
    USERS_FILE.write_text(json.dumps(seeded))
    print(f"seeded {count} users, {len(chunks)} chunks each")


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {"n": len(ordered), "mean": round(statistics.fmean(ordered), 3), "p50": round(ordered[len(ordered) // 2], 3),
            "p95": round(ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))], 3), "max": round(ordered[-1], 3)}


def _append(out: str, row: dict) -> None:
    target = RESULTS / out
    results = json.loads(target.read_text()) if target.exists() else []
    target.write_text(json.dumps([*results, row], ensure_ascii=False, indent=2))
    print(json.dumps(row, ensure_ascii=False))


def generate(modules: int, label: str, out: str = "generation.json", max_seconds: int | None = None) -> None:
    users = json.loads(USERS_FILE.read_text())
    if modules > len(users):
        raise SystemExit("seed more users than modules: each document runs one generation at a time")
    with SessionLocal() as db:
        active = db.scalar(select(func.count(Job.id)).where(Job.status.in_([JobStatus.queued, JobStatus.running])))
    if active:
        raise SystemExit(f"{active} jobs are still active: they would take worker slots and skew the result")
    started_at = datetime.now(UTC)
    t0 = time.perf_counter()
    timed_out = False
    job_ids = []
    with httpx.Client(timeout=60) as client:
        for user in users[:modules]:
            # A container that has just been recreated answers 502 for a few seconds.
            for _ in range(20):
                response = client.post(f"{BASE}/modules/generate", params={"document_id": user["document_id"]},
                                       headers={"Authorization": f"Bearer {user['token']}"})
                if response.status_code < 500:
                    break
                time.sleep(3)
            response.raise_for_status()
            job_ids.append(uuid.UUID(response.json()["job_id"]))
    print(f"queued {len(job_ids)} jobs in {time.perf_counter() - t0:.1f}s", flush=True)
    while True:
        with SessionLocal() as db:
            statuses = list(db.scalars(select(Job.status).where(Job.id.in_(job_ids))))
        if all(s in (JobStatus.done, JobStatus.failed) for s in statuses):
            break
        if max_seconds and time.perf_counter() - t0 > max_seconds:
            # Queued jobs would keep spending tokens after the run has been written off.
            with SessionLocal() as db:
                db.query(Job).filter(Job.id.in_(job_ids), Job.status == JobStatus.queued).update(
                    {"status": JobStatus.failed, "last_error": "run hit its time limit"})
                db.commit()
            timed_out = True
            break
        time.sleep(3)
    wall = time.perf_counter() - t0
    with SessionLocal() as db:
        attempts = sum(db.scalars(select(Job.attempts).where(Job.id.in_(job_ids))))
        failed = db.scalars(select(Job).where(Job.id.in_(job_ids), Job.status != JobStatus.done)).all()
        errors = sorted({(j.last_error or "")[:200] for j in failed})
        documents = [uuid.UUID(u["document_id"]) for u in users[:modules]]
        rows = db.scalars(select(Module).where(Module.document_id.in_(documents), Module.created_at >= started_at)).all()
        timings = [m.generation_timings for m in rows if m.generation_timings]
    spans = [s for t in timings for s in t["spans"]]
    row = {
        "label": label, "modules": modules, "done": len(job_ids) - len(failed), "failed": len(failed), "errors": errors,
        "wall_seconds": round(wall, 1), "modules_per_hour": round(3600 * (len(job_ids) - len(failed)) / wall, 1),
        "module_seconds": _stats([t["total_seconds"] for t in timings]),
        "timed_out": timed_out, "job_attempts": attempts,
        "prompt_tokens": sum(s.get("prompt_tokens") or 0 for s in spans),
        "completion_tokens": sum(s.get("completion_tokens") or 0 for s in spans),
        "served_models": sorted({s.get("served_model") for s in spans if s.get("served_model")}),
        "llm_calls_per_module": round(sum(s["kind"] == "llm" for s in spans) / max(1, len(timings)), 1),
        "retrievals_per_module": round(sum(s["kind"] == "rerank" for s in spans) / max(1, len(timings)), 1),
        "rerank_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "rerank"]),
        "rerank_fallbacks": sum(bool(s.get("fallback")) for s in spans if s["kind"] == "rerank"),
        "rerank_retries": sum(s.get("retries") or 0 for s in spans if s["kind"] == "rerank"),
        "embed_query_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "embed_query"]),
        "vector_search_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "vector_search"]),
        "bm25_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "bm25"]),
        "llm_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "llm"]),
        "llm_rate_limited": sum(bool(s.get("rate_limited")) for s in spans if s["kind"] == "llm"),
        "llm_wait_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "llm_wait"]),
        "llm_backoff_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "llm_backoff"]),
    }
    _append(out, row)


def chat(concurrency: int, messages: int, label: str, out: str) -> None:
    users = json.loads(USERS_FILE.read_text())

    def send(i: int) -> tuple[int, float]:
        user = users[i % len(users)]
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=300) as client:
                response = client.post(f"{BASE}/courses/{user['course_id']}/chat",
                                       json={"message": QUESTIONS[i % len(QUESTIONS)]},
                                       headers={"Authorization": f"Bearer {user['token']}"})
            return response.status_code, time.perf_counter() - started
        except httpx.HTTPError:
            return 0, time.perf_counter() - started

    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(send, range(messages)))
    wall = time.perf_counter() - t0
    codes: dict[str, int] = {}
    for code, _ in results:
        codes[str(code)] = codes.get(str(code), 0) + 1
    _append(out, {"label": label, "concurrency": concurrency, "messages": messages, "status_codes": codes,
                  "failed": sum(code != 200 for code, _ in results), "wall_seconds": round(wall, 1),
                  "messages_per_minute": round(60 * codes.get("200", 0) / wall, 1),
                  "latency_seconds": _stats([s for code, s in results if code == 200])})


def chat_stream(concurrency: int, messages: int, label: str, out: str, rate: float | None = None) -> None:
    """Sends messages to the stream endpoint and records the time to the first word and to the end. With `rate`,
    they arrive at that many per second whatever the system does, as readers would; otherwise all at once."""
    users = json.loads(USERS_FILE.read_text())
    gate = asyncio.Semaphore(concurrency)
    rows: list[dict] = []

    async def send(client: httpx.AsyncClient, i: int) -> None:
        if rate:
            await asyncio.sleep(i / rate)
        user = users[i % len(users)]
        row = {"status": 0, "first": None, "total": None, "ended": None}
        async with gate:
            started = time.perf_counter()
            try:
                async with client.stream("POST", f"{BASE}/courses/{user['course_id']}/chat/stream",
                                         json={"message": QUESTIONS[i % len(QUESTIONS)]},
                                         headers={"Authorization": f"Bearer {user['token']}"}) as response:
                    row["status"] = response.status_code
                    async for line in response.aiter_lines():
                        if line == "event: delta" and row["first"] is None:
                            row["first"] = time.perf_counter() - started
                        elif line in ("event: done", "event: error"):
                            row["ended"] = line.removeprefix("event: ")
                    row["total"] = time.perf_counter() - started
            except httpx.HTTPError as error:
                row["status"] = type(error).__name__
        rows.append(row)

    async def run() -> None:
        limits = httpx.Limits(max_connections=None, max_keepalive_connections=None)
        async with httpx.AsyncClient(timeout=300, limits=limits) as client:
            await asyncio.gather(*(send(client, i) for i in range(messages)))

    t0 = time.perf_counter()
    asyncio.run(run())
    wall = time.perf_counter() - t0
    done = [r for r in rows if r["ended"] == "done"]
    codes: dict[str, int] = {}
    for row in rows:
        key = str(row["status"]) if row["ended"] != "error" else "stream-error"
        codes[key] = codes.get(key, 0) + 1
    _append(out, {"label": label, "concurrency": concurrency, "rate": rate, "messages": messages, "status_codes": codes,
                  "failed": messages - len(done), "wall_seconds": round(wall, 1),
                  "messages_per_minute": round(60 * len(done) / wall, 1),
                  "first_word_seconds": _stats([r["first"] for r in done if r["first"] is not None]),
                  "latency_seconds": _stats([r["total"] for r in done])})


def check(out: str, token_cap: int) -> None:
    target = RESULTS / out
    if not target.exists():
        sys.exit("the run wrote no result")
    rows = json.loads(target.read_text())
    last = rows[-1]
    # A chat message is estimated, since the API does not report its tokens: two calls of about 5,000 tokens.
    tokens = sum(r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) + r.get("messages", 0) * 10_000 for r in rows)
    print(f"{last['label']}: failed {last.get('failed')}, tokens so far {tokens:,}")
    if tokens > token_cap:
        sys.exit("token cap reached")
    errors = "; ".join(last.get("errors", []))
    # Rate limits under load are a result to record, so only a run that cannot succeed stops the series.
    if re.search(r"credit|billing|insufficient|authentication|invalid.*api.?key", errors, re.IGNORECASE):
        sys.exit("BILLING " + errors[:400])
    if last.get("failed", 0) >= (last.get("modules") or last.get("messages")):
        sys.exit("every item in this run failed: " + errors[:400])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["seed", "generate", "chat", "chat-stream", "check"])
    parser.add_argument("--users", type=int, default=40)
    parser.add_argument("--modules", type=int, default=8)
    parser.add_argument("--label", default="run")
    parser.add_argument("--out", default="generation.json")
    parser.add_argument("--max-seconds", type=int)
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--messages", type=int, default=50)
    parser.add_argument("--rate", type=float)
    parser.add_argument("--token-cap", type=int, default=30_000_000)
    args = parser.parse_args()
    if args.step == "seed":
        seed(args.users)
    elif args.step == "generate":
        generate(args.modules, args.label, args.out, args.max_seconds)
    elif args.step == "chat":
        chat(args.concurrency, args.messages, args.label, args.out)
    elif args.step == "chat-stream":
        chat_stream(args.concurrency, args.messages, args.label, args.out, args.rate)
    else:
        check(args.out, args.token_cap)


if __name__ == "__main__":
    main()
