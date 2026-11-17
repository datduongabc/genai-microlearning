"""One flashcard set, then one video script, for --alias, through the HTTP API as the lecturer."""
import argparse, json, time, urllib.request
from sqlalchemy import select, update
from app.auth.security import create_access_token
from app.db import RequestSessionLocal as SessionLocal
from app.models import User

DOC = "596bde0d-cd8b-4a12-80c5-504b69a198bc"
EMAIL = "baseline.admin@example.com"
BASE = "http://localhost:8000"

def reset():
    # The daily limit for accounts without a live Moodle link; the run needs 8 generations.
    with SessionLocal() as db:
        db.execute(update(User).where(User.email == EMAIL).values(generation_count=0))
        db.commit()

def call(method, path, token, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())

def wait(path, token):
    while True:
        d = call("GET", path, token)
        if d["status"] != "generating":
            return d
        time.sleep(5)

p = argparse.ArgumentParser(); p.add_argument("--alias"); p.add_argument("--only", default="flashcards,video")
a = p.parse_args()
with SessionLocal() as db:
    uid = db.scalar(select(User.id).where(User.email == EMAIL))
token = create_access_token(uid)
body = {"focus": "FCFS và Round Robin", "output_language": "vi", "model_alias": a.alias}
for kind, path in (("flashcards", "/flashcard-sets"), ("video", "/videos")):
    if kind not in a.only:
        continue
    reset()
    t0 = time.time()
    r = call("POST", f"{path}?document_id={DOC}", token, body)
    d = wait(f"{path}/{r['id']}", token)
    print(json.dumps({"alias": a.alias, "kind": kind, "id": str(r["id"]), "status": d["status"],
                      "wall_seconds": round(time.time() - t0, 1), "error": d.get("error")}), flush=True)
