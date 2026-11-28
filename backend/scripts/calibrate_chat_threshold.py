"""The reranker score below which the course chat says the documents do not cover a question, from measurements.

    BENCHMARK_DATABASE_URL=... PYTHONPATH=. python scripts/calibrate_chat_threshold.py \\
        --questions ../docs/measurements/chunking/truy_van.json \\
        --off-topic ../docs/measurements/chat-threshold/off_topic.json \\
        --jev-cache ../docs/measurements/chunking/jev_cache.jsonl ../docs/measurements/chunking-gemini/jev_cache_added.jsonl \\
        --out ../docs/measurements/chat-threshold

Each question is searched as the chat searches it (search_for_chat: dense plus BM25, 30 candidates) and the
candidates are scored by Jev, as rerank_scored does; the best score is what the threshold is compared with.
The 250 benchmark questions are asked in their own subject's course and must be kept; the off-topic questions
are asked in all five courses and should be refused. Jev answers are cached by (model, question, passage), the
key benchmark_chunking.py uses, so the benchmark questions cost nothing again; only new pairs are asked and are
added to `jev_cache.jsonl` in the output folder. Run against `benchmark_gemini` (the current chunking, Gemini
vectors), with the gateway and the TypeSafe key of the backend.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Course
from app.retrieval import reranker
from app.retrieval.search import invalidate_sparse_cache, search_for_chat

SUBJECTS = ["advanced-programming", "calculus 1", "machine-learning", "operating-system", "scientific-socialism"]
CANDIDATES = 30


def _key(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode() if isinstance(part, str) else part)
        h.update(b"\0")
    return h.hexdigest()


def _cached_jev(known: dict[str, float], added: Path):
    ask = reranker._ask_jev

    def cached(question: str, passage: str, attempts: int = reranker.MAX_ATTEMPTS) -> tuple[float, int]:
        key = _key(settings.jev_model, question, passage)
        if key not in known:
            known[key] = ask(question, passage, attempts)[0]
            with added.open("a", encoding="utf-8") as file:
                file.write(json.dumps({"key": key, "value": known[key]}) + "\n")
        return known[key], 0
    return cached


def _top_score(db: Session, course_id, question: str) -> float | None:
    candidates = search_for_chat(db, course_id, question, candidate_k=CANDIDATES)
    span: dict = {}
    scores = reranker._jev_scores(question, candidates, span)
    if span.get("fallback"):
        raise RuntimeError(f"Jev fell back on {question!r}; its scores would be the search order")
    return max(scores) if scores else None


def kept_and_refused(in_domain: list[float], off_topic: list[float], threshold: float) -> tuple[float, float]:
    """Share of the course's own questions still answered, and of the off-topic ones refused, at `threshold`."""
    return (sum(s >= threshold for s in in_domain) / len(in_domain),
            sum(s < threshold for s in off_topic) / len(off_topic))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--off-topic", type=Path, required=True)
    parser.add_argument("--jev-cache", type=Path, nargs="*", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    added = args.out / "jev_cache.jsonl"
    known: dict[str, float] = {}
    for path in [*args.jev_cache, added]:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                known[entry["key"]] = entry["value"]
    reranker._ask_jev = _cached_jev(known, added)

    engine = create_engine(os.environ["BENCHMARK_DATABASE_URL"])
    rows = []
    with Session(engine) as db:
        courses = {s: db.query(Course).filter(Course.name == f"{s} | moi").one().id for s in SUBJECTS}
        for course_id in courses.values():
            invalidate_sparse_cache(course_id)
        for q in json.loads(args.questions.read_text(encoding="utf-8")):
            rows.append({"kind": "course", "subject": q["course"], "question": q["cau_hoi"],
                         "top_score": _top_score(db, courses[q["course"]], q["cau_hoi"])})
        for q in json.loads(args.off_topic.read_text(encoding="utf-8")):
            for subject, course_id in courses.items():
                rows.append({"kind": "off_topic", "group": q["group"], "subject": subject, "question": q["question"],
                             "top_score": _top_score(db, course_id, q["question"])})
    (args.out / "scores.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    in_domain = sorted(r["top_score"] for r in rows if r["kind"] == "course")
    off_topic = [r["top_score"] for r in rows if r["kind"] == "off_topic"]
    table = []
    for threshold in (0.01, 0.02, 0.03, 0.05, 0.08, 0.1, 0.15, 0.2, 0.3):
        kept, refused = kept_and_refused(in_domain, off_topic, threshold)
        table.append({"threshold": threshold, "course_questions_kept": round(kept, 4), "off_topic_refused": round(refused, 4)})
    summary = {"course_questions": len(in_domain), "off_topic_queries": len(off_topic),
               "lowest_course_scores": in_domain[:10], "jev_model": settings.jev_model, "table": table}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
