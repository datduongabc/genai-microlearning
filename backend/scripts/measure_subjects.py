"""Per subject: upload every file (one at a time), ask its chat-eval questions, generate two modules.

    uv run python -u scripts/measure_subjects.py out.jsonl "advanced-programming,calculus 1"

Talks to the API of the compose stack on localhost:8000 with a new account. Writes one JSON line per
event to the output file and prints it. See docs/measurements/2026-11-08-jev-four-subjects.md.

On a deployment, where registration is closed: API=https://<cloudfront>/api MODEL=openai-fast
EMAIL=... PASSWORD=... (an account the administrator made). Each chat row also carries the cited
passages as `contexts` and whether the question's source chunk was cited, so chat_eval_ragas.py can
judge the file.
"""

import json
import os
import secrets
import sys
import time
from pathlib import Path

import httpx

B = os.environ.get("API", "http://localhost:8000")
REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "docs/documents"
SUBJECTS = sys.argv[2].split(",")
QUESTIONS = json.loads((REPO / "docs/measurements/chat-eval/questions.json").read_text(encoding="utf-8"))
OUT = open(sys.argv[1], "a", encoding="utf-8")  # noqa: SIM115 -- appended to for the whole run
MODEL = os.environ.get("MODEL", "gemini-flash")


def emit(**row):
    OUT.write(json.dumps(row, ensure_ascii=False) + "\n"); OUT.flush()
    print({k: v for k, v in row.items() if k not in ("answer", "contexts")}, flush=True)


def send_file(client: httpx.Client, target: dict, data: bytes) -> None:
    """Where the API said to send it: a presigned S3 form POST (no token, which S3 rejects), or a PUT to the API."""
    if target["method"] == "POST":
        httpx.post(target["url"], data=target["fields"], files={"file": data}, timeout=300).raise_for_status()
    else:
        client.put(target["url"], content=data).raise_for_status()


c = httpx.Client(base_url=B, timeout=300)
email, pw = os.environ.get("EMAIL"), os.environ.get("PASSWORD")
if not email:
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
        send_file(c, up["upload"], data)
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

    passages = {}
    for document_id in docs:
        offset = 0
        while True:
            page = c.get(f"/documents/{document_id}/chunks", params={"offset": offset, "limit": 50}).json()
            passages.update({p["chunk_id"]: p["content"] for p in page["items"]})
            offset += 50
            if offset >= page["total"]:
                break

    for q in [q for q in QUESTIONS if q["course"] == subject]:
        t0 = time.time()
        m, event, first_word = {}, None, None
        lines = []
        with c.stream("POST", f"/courses/{cid}/chat/stream", json={"message": q["cau_hoi"]}) as r:
            for line in r.iter_lines():
                lines.append(line)
                if first_word is None and line.startswith("event: delta"):
                    first_word = time.time() - t0
        for line in lines:
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            # "done" carries the saved message, "error" a detail; deltas are only its pieces.
            elif line.startswith("data: ") and event in ("done", "error"):
                m = json.loads(line.removeprefix("data: "))
        text = m.get("content") or m.get("detail") or "\n".join(lines)
        dt = time.time() - t0
        contexts = [passages.get(cited["chunk_id"], "") for cited in m.get("citations") or []]
        # The chunk ids are new in this database, so the question's source chunk is found by its text.
        emit(event="chat", subject=subject, course=subject, chunk_id=q["chunk_id"], served_models=[MODEL], question=q["cau_hoi"], status=r.status_code,
             seconds=round(dt, 2), first_word_s=round(first_word, 2) if first_word else None,
             citations=len(contexts), source_cited=q["reference"].strip() in [t.strip() for t in contexts],
             source_ingested=q["reference"].strip() in [t.strip() for t in passages.values()],
             answer=text, contexts=contexts)

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
