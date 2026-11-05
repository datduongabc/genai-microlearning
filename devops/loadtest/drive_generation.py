"""Seeds users with one course and document each, then queues module generations through the API.

    python drive_generation.py seed --users 40
    python drive_generation.py generate --modules 8 --label workers-1x4

`seed` reads and embeds one real lecture once and copies its chunks to every course. `generate` waits
for the jobs, then adds a row with throughput and per-stage timings to /results/generation.json."""

import argparse
import json
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


def generate(modules: int, label: str) -> None:
    users = json.loads(USERS_FILE.read_text())
    if modules > len(users):
        raise SystemExit("seed more users than modules: each document runs one generation at a time")
    with SessionLocal() as db:
        active = db.scalar(select(func.count(Job.id)).where(Job.status.in_([JobStatus.queued, JobStatus.running])))
    if active:
        raise SystemExit(f"{active} jobs are still active: they would take worker slots and skew the result")
    started_at = datetime.now(UTC)
    t0 = time.perf_counter()
    job_ids = []
    with httpx.Client(timeout=60) as client:
        for user in users[:modules]:
            response = client.post(f"{BASE}/modules/generate", params={"document_id": user["document_id"]},
                                   headers={"Authorization": f"Bearer {user['token']}"})
            response.raise_for_status()
            job_ids.append(uuid.UUID(response.json()["job_id"]))
    print(f"queued {len(job_ids)} jobs in {time.perf_counter() - t0:.1f}s", flush=True)
    while True:
        with SessionLocal() as db:
            statuses = list(db.scalars(select(Job.status).where(Job.id.in_(job_ids))))
        if all(s in (JobStatus.done, JobStatus.failed) for s in statuses):
            break
        time.sleep(3)
    wall = time.perf_counter() - t0
    with SessionLocal() as db:
        failed = db.scalars(select(Job).where(Job.id.in_(job_ids), Job.status == JobStatus.failed)).all()
        errors = sorted({(j.last_error or "")[:200] for j in failed})
        documents = [uuid.UUID(u["document_id"]) for u in users[:modules]]
        rows = db.scalars(select(Module).where(Module.document_id.in_(documents), Module.created_at >= started_at)).all()
        timings = [m.generation_timings for m in rows if m.generation_timings]
    spans = [s for t in timings for s in t["spans"]]
    row = {
        "label": label, "modules": modules, "done": len(job_ids) - len(failed), "failed": len(failed), "errors": errors,
        "wall_seconds": round(wall, 1), "modules_per_hour": round(3600 * (len(job_ids) - len(failed)) / wall, 1),
        "module_seconds": _stats([t["total_seconds"] for t in timings]),
        "llm_calls_per_module": round(sum(s["kind"] == "llm" for s in spans) / max(1, len(timings)), 1),
        "retrievals_per_module": round(sum(s["kind"] == "rerank" for s in spans) / max(1, len(timings)), 1),
        "rerank_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "rerank"]),
        "embed_query_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "embed_query"]),
        "vector_search_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "vector_search"]),
        "bm25_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "bm25"]),
        "llm_seconds": _stats([s["seconds"] for s in spans if s["kind"] == "llm"]),
    }
    target = RESULTS / "generation.json"
    results = json.loads(target.read_text()) if target.exists() else []
    target.write_text(json.dumps([*results, row], ensure_ascii=False, indent=2))
    print(json.dumps(row, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["seed", "generate"])
    parser.add_argument("--users", type=int, default=40)
    parser.add_argument("--modules", type=int, default=8)
    parser.add_argument("--label", default="run")
    args = parser.parse_args()
    if args.step == "seed":
        seed(args.users)
    else:
        generate(args.modules, args.label)


if __name__ == "__main__":
    main()
