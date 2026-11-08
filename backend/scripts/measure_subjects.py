"""Per subject: upload every file (one at a time), ask its chat-eval questions, generate two modules.

    uv run python -u scripts/measure_subjects.py out.jsonl "advanced-programming,calculus 1"

Talks to the API of the compose stack on localhost:8000 with a new account. Writes one JSON line per
event to the output file and prints it. See docs/measurements/2026-11-08-jev-four-subjects.md.
"""

import json
import secrets
import sys
import time
from pathlib import Path

import httpx

B = "http://localhost:8000"
REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "docs/documents"
SUBJECTS = sys.argv[2].split(",")
QUESTIONS = json.loads((REPO / "docs/measurements/chat-eval/questions.json").read_text(encoding="utf-8"))
OUT = open(sys.argv[1], "a", encoding="utf-8")  # noqa: SIM115 -- appended to for the whole run
MODEL = "gemini-fast"


def emit(**row):
    OUT.write(json.dumps(row, ensure_ascii=False) + "\n"); OUT.flush()
    print({k: v for k, v in row.items() if k not in ("answer",)}, flush=True)


c = httpx.Client(base_url=B, timeout=300)
email, pw = f"measure-{secrets.token_hex(3)}@example.com", "Measure-pass-" + secrets.token_hex(4)
c.post("/auth/register", json={"email": email, "name": "Measure", "password": pw}).raise_for_status()
c.headers["Authorization"] = "Bearer " + c.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]
emit(event="account", email=email)

for subject in SUBJECTS:
    cid = c.post("/courses", json={"name": f"Đo {subject}"}).json()["id"]
    docs = []
    for path in sorted((ROOT / subject).iterdir()):
        data = path.read_bytes()
        t0 = time.time()
        up = c.post("/documents/uploads", params={"course_id": cid}, json={"filename": path.name, "size": len(data)})
        up.raise_for_status(); up = up.json()
        c.put(up["upload"]["url"], content=data).raise_for_status()
        t_sent = time.time()
        c.post(f"/documents/{up['document_id']}/complete").raise_for_status()
        while True:
            doc = next(d for d in c.get("/documents", params={"course_id": cid}).json() if d["id"] == up["document_id"])
            if doc["status"] in ("embedded", "failed"):
                break
            time.sleep(2)
        docs.append(up["document_id"])
        emit(event="ingest", subject=subject, file=path.name, mb=round(len(data) / 2**20, 2), status=doc["status"],
             upload_s=round(t_sent - t0, 2), total_s=round(time.time() - t0, 1), document_id=up["document_id"])

    for q in [q for q in QUESTIONS if q["course"] == subject]:
        t0 = time.time()
        r = c.post(f"/courses/{cid}/chat", json={"message": q["cau_hoi"]})
        dt = time.time() - t0
        m = r.json().get("message", {}) if r.status_code == 200 else {}
        emit(event="chat", subject=subject, status=r.status_code, seconds=round(dt, 2),
             citations=len(m.get("citations") or []), answer=(m.get("content") or r.text)[:400])

    # Two modules: the first two files of the subject, in name order.
    for document_id in docs[:2]:
        t0 = time.time()
        g = c.post("/modules/generate", params={"document_id": document_id}, json={"model_alias": MODEL})
        if g.status_code != 200:
            emit(event="module", subject=subject, status=g.status_code, error=g.text[:200]); continue
        job = g.json()["job_id"]
        while True:
            s = c.get(f"/modules/jobs/{job}").json()
            if s.get("status") in ("done", "failed"):
                break
            time.sleep(5)
        emit(event="module", subject=subject, document_id=document_id, status=s.get("status"),
             seconds=round(time.time() - t0, 1), module_id=s.get("module_id"), error=s.get("error"))
