"""Measures how many candidates the reranker needs: fewer means less CPU per retrieval.

Uses the questions and labels of the five-subject benchmark, which name chunks of that benchmark's
database, so BENCHMARK_DATABASE_URL must point at it. Reranking stays sequential to time it fairly.

    python scripts/compare_rerank_candidates.py questions.json out.json --candidates 30 20 15 10
"""

import argparse
import json
import os
import statistics
import time
import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.retrieval.reranker import rerank_scored
from app.retrieval.search import hybrid_search

CUTOFFS = (1, 3, 5, 8)


def measure(db: Session, questions: list[dict], candidate_k: int) -> dict:
    ranks, seconds = [], []
    for question in questions:
        course_id = uuid.UUID(question["course_id"])
        candidates = hybrid_search(db, course_id, question["cau_hoi"], top_k=candidate_k, candidate_k=candidate_k)
        started = time.perf_counter()
        ranked = rerank_scored(question["cau_hoi"], candidates)
        seconds.append(time.perf_counter() - started)
        ids = [str(chunk.id) for chunk, _ in ranked]
        ranks.append(ids.index(question["chunk_id"]) + 1 if question["chunk_id"] in ids else None)
    count = len(ranks)
    found = [r for r in ranks if r is not None]
    ordered = sorted(seconds)
    return {
        "candidate_k": candidate_k, "questions": count,
        **{f"recall@{k}": round(sum(r <= k for r in found) / count, 4) for k in CUTOFFS},
        "mrr": round(sum(1 / r for r in found) / count, 4),
        "rerank_mean_seconds": round(statistics.fmean(seconds), 3),
        "rerank_p95_seconds": round(ordered[round(0.95 * (count - 1))], 3),
        "ranks": ranks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("questions")
    parser.add_argument("output")
    parser.add_argument("--candidates", type=int, nargs="+", default=[30, 20, 15, 10])
    args = parser.parse_args()
    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    results = []
    with Session(create_engine(os.environ["BENCHMARK_DATABASE_URL"])) as db:
        for candidate_k in args.candidates:
            row = measure(db, questions, candidate_k)
            results.append(row)
            print({k: v for k, v in row.items() if k != "ranks"}, flush=True)
            with open(args.output, "w", encoding="utf-8") as out:
                json.dump(results, out)


if __name__ == "__main__":
    main()
