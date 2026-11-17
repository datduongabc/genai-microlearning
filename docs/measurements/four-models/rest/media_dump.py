"""Dump the flashcard sets and videos named in media-runs.jsonl, with summary numbers."""
import json, sys
from app.db import WorkerSessionLocal
from app.models import FlashcardSet, Video
runs = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
out = []
with WorkerSessionLocal() as db:
    for r in runs:
        if r["kind"] == "flashcards":
            s = db.get(FlashcardSet, r["id"])
            cards = s.cards
            row = r | {"status": s.status.value, "cards": len(cards), "flagged": sum(bool(c.get("flag")) for c in cards),
                       "no_citation": sum(not c["citations"] for c in cards), "cost": s.cost, "served_models": s.served_models,
                       "items": cards}
        else:
            v = db.get(Video, r["id"])
            sc = v.scenes
            row = r | {"status": v.status.value, "scenes": len(sc), "illustrations": sum(s["kind"] == "illustration" for s in sc),
                       "scenes_flagged": sum(bool(s.get("flags")) for s in sc), "flags": sum(len(s.get("flags") or []) for s in sc),
                       "narration_chars": sum(len(s["narration"]) for s in sc), "error": v.error, "cost": v.cost,
                       "duration_seconds": v.duration_seconds, "served_models": v.served_models, "items": sc}
        out.append(row)
for row in out:
    print(json.dumps({k: v for k, v in row.items() if k != "items"}, ensure_ascii=False, default=str))
if len(sys.argv) > 2:
    open(sys.argv[2], "w").write("\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in out) + "\n")
