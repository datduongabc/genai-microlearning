"""Draw the pictures of one video, then render it, through the API as its lecturer."""
import json, sys, time, urllib.request
from sqlalchemy import select
from app.auth.security import create_access_token
from app.db import RequestSessionLocal
from app.models import User
VID = sys.argv[1]; BASE = "http://localhost:8000"
with RequestSessionLocal() as db:
    token = create_access_token(db.scalar(select(User.id).where(User.email == "baseline.admin@example.com")))
def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e: print("HTTP", e.code, e.read().decode()); raise
def wait():
    while True:
        d = call("GET", f"/videos/{VID}")
        if d["job"] is None or d["job"]["status"] in ("failed", "succeeded", "done"): return d
        time.sleep(5)
d = call("GET", f"/videos/{VID}")
print("before", d["status"], "problems", d["render_problems"], "warnings", d["render_warnings"], "est", d["estimated_seconds"], flush=True)
for step, path in (("images", f"/videos/{VID}/illustrations"), ("render", f"/videos/{VID}/render")):
    t0 = time.time(); r = call("POST", path, {} if step == "images" else None); print(step, r, flush=True)
    d = wait(); print(step, "->", d["status"], "wall", round(time.time() - t0, 1), "error", d["error"], "duration", d["duration_seconds"], flush=True)
print(json.dumps(d["cost"]), flush=True)
