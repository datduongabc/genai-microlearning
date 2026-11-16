"""Rubric scores (docs/rubric.md, 9 criteria) of stored modules, each by several judges, outside a study.

    PYTHONPATH=. python scripts/judge_modules.py --out ratings.jsonl --judges claude-fast,openai-fast <module_id>...

Each module becomes the packet a study rater would get (app/generation/study_packets.py: the module's
components and its reference chunks), and each judge scores it once, pinned with fallbacks off; the
model the gateway actually served is recorded. A judge of the same family as a model that generated
the module is refused. Resumed: (module, judge) pairs already in the output file are skipped. Run it
with DATABASE_URL, the owner's URL.
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from app.db import SessionLocal
from app.generation.evaluation import judge_module
from app.generation.study_packets import module_packet, reference_chunks
from app.llm.gateway import capture_models, experiment_mode, using_model
from app.llm.judge import model_family
from app.models import Module

CRITERIA = ("accuracy", "completeness", "consistency", "hallucination_score", "slide_quality", "quiz_quality",
            "bloom_fidelity", "query_alignment")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("modules", nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--judges", default="claude-fast,openai-fast")
    args = parser.parse_args()
    judges = args.judges.split(",")
    done = set()
    if args.out.exists():
        done = {(r["module_id"], r["judge"]) for r in map(json.loads, args.out.read_text(encoding="utf-8").splitlines())}
    with SessionLocal() as db, args.out.open("a", encoding="utf-8") as sink:
        for module_id in args.modules:
            module = db.get(Module, uuid.UUID(module_id))
            generators = {model_family(c["served_model"]) for c in module.generation_models or []}
            packet = module_packet(module, reference_chunks(db, module)).model_copy(
                update={"anon_id": "M-" + module_id[:8]})
            for judge in judges:
                if (module_id, judge) in done:
                    continue
                started = time.perf_counter()
                with experiment_mode(), using_model(judge), capture_models() as calls:
                    rating = judge_module(packet)
                served = sorted({c["served_model"] for c in calls})
                if generators & {model_family(m) for m in served}:
                    sys.exit(f"{judge} served {served}, the family that generated {module_id}")
                row = {"module_id": module_id, "title": module.title, "judge": judge, "served_models": served,
                       "seconds": round(time.perf_counter() - started, 1),
                       "scores": {k: getattr(rating, k).score for k in CRITERIA},
                       "findings": [f.model_dump() for f in rating.hallucination_findings],
                       "rating": rating.model_dump()}
                sink.write(json.dumps(row, ensure_ascii=False) + "\n")
                sink.flush()
                print(module_id[:8], judge, served, row["seconds"], row["scores"], flush=True)


if __name__ == "__main__":
    main()
