import json, os, time, httpx
U = "https://d2g7nsigk1pw6c.cloudfront.net/api"
R = "/home/asus/projects/do-an/docs/documents/"
import glob
FILES = [glob.glob(R + "calculus 1/1. *.pdf")[0], R + "operating-system/Chapter 5_CPU Scheduling.pdf"]
QS = ["Round Robin khác FCFS ở điểm nào?", "What is the convoy effect?", "Thời gian chờ trung bình của SJF được tính thế nào?"]
out = {}
with httpx.Client(timeout=120) as c:
    tok = c.post(f"{U}/auth/login", json={"email": os.environ["EMAIL"], "password": os.environ["PASSWORD"]}).json()["access_token"]
    c.headers["Authorization"] = f"Bearer {tok}"
    cid = c.post(f"{U}/courses", json={"name": "Smoke test 11-17c"}).json()["id"]; out["course"] = cid; print("course", cid, flush=True)
    for f in FILES:
        data = open(f, "rb").read(); name = os.path.basename(f)
        t0 = time.time()
        up = c.post(f"{U}/documents/uploads", params={"course_id": cid}, json={"filename": name, "size": len(data)}).json()
        did, t = up["document_id"], up["upload"]
        r = httpx.post(t["url"], data=t["fields"], files={"file": (name, data)}, timeout=300)
        c.post(f"{U}/documents/{did}/complete").raise_for_status()
        t1 = time.time()
        while True:
            s = c.get(f"{U}/documents/{did}/status").json()
            if s["status"] in ("embedded", "failed") or time.time() - t1 > 1500: break
            time.sleep(3)
        ch = c.get(f"{U}/documents/{did}/chunks").json()
        chunks = ch if isinstance(ch, list) else ch.get("chunks", ch.get("items", []))
        rec = {"file": name, "mb": round(len(data)/2**20, 2), "upload_s": round(t1-t0, 1), "ingest_s": round(time.time()-t1, 1), "status": s["status"], "chunks": len(chunks), "status_detail": {k: v for k, v in s.items() if k not in ("id",)}}
        print(json.dumps(rec, ensure_ascii=False), flush=True); out.setdefault("docs", []).append(rec)
    for q in QS:
        t0 = time.time(); first = None; final = None
        with c.stream("POST", f"{U}/courses/{cid}/chat/stream", json={"message": q}) as s:
            ev = None
            for line in s.iter_lines():
                if line.startswith("event:"): ev = line[6:].strip()
                elif line.startswith("data:"):
                    if ev == "delta" and first is None: first = time.time() - t0
                    if ev == "done": final = json.loads(line[5:])
        rec = {"q": q, "status": s.status_code, "first_token_s": round(first or -1, 2), "total_s": round(time.time()-t0, 2), "citations": len((final or {}).get("citations") or []), "answer": ((final or {}).get("content") or "")[:160]}
        print(json.dumps(rec, ensure_ascii=False), flush=True); out.setdefault("chat", []).append(rec)
json.dump(out, open(os.environ["OUT"], "w"), ensure_ascii=False, indent=1)
