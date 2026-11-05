"""Compares Jev with the Jina reranker and with no rerank, on the same hybrid search candidates.

Jev reads text, so each call sends the question and the candidate chunks to TypeSafe. It needs TYPESAFE_API_KEY, and the
same BENCHMARK_DATABASE_URL as benchmark_rerank.py. Answers are cached, so a repeated run spends nothing, and the cache
holds only scores and token counts. `dev` picks the variant and `test` measures it, so the wording is not tuned on the
questions that report the result.

    python scripts/compare_jev_rerank.py questions.json out.json --subset dev --variants none jina jev-pair jev-batch
    python scripts/compare_jev_rerank.py questions.json out.json --subset test --variants none jina jev-pair
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_rerank import _chi_so, _mcnemar

from app.retrieval.reranker import rerank_scored
from app.retrieval.search import _adaptive_k_largest_gap, hybrid_search

URL = "https://api.typesafe.ai/v1/systemone"
# `jev-latest` moves with each release, which would change results between runs.
MODEL = "jev-1.14.0"
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000
CANDIDATES = 30
# Chat and quizzes use a fixed count; learning objectives and content use Adaptive-k with a floor of eight.
ADAPTIVE_FLOORS = (4, 8)
PAIR_QUESTION = "Does the passage contain the information needed to answer the query?"
BATCH_QUESTION = "Does passage {key} contain the information needed to answer the query?"
MAX_ATTEMPTS = 6
PARALLEL_CALLS = 8


def in_subset(index: int, subset: str) -> bool:
    """The questions are ordered by subject, so every fifth one gives each subject the same share of dev."""
    return subset == "all" or (index % 5 == 0) == (subset == "dev")


class JevClient:
    def __init__(self, key: str, cache_path: Path, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(headers={"Authorization": f"Bearer {key}"}, timeout=60, transport=transport)
        self._cache_path = cache_path
        self._cache = {}
        self.retries = 0
        if cache_path.exists():
            with cache_path.open(encoding="utf-8") as lines:
                self._cache = {row["key"]: row for row in map(json.loads, lines)}

    def ask(self, state: dict, questions: dict) -> dict:
        """Returns {"nouls": {name: probability}, "input_tokens": n, "seconds": s}; seconds is 0 for a cached answer."""
        body = {"model": MODEL, "state": state, "questions": questions}
        key = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if key in self._cache:
            return {**self._cache[key], "seconds": 0.0}
        started = time.perf_counter()
        for attempt in range(MAX_ATTEMPTS):
            response = self._client.post(URL, json=body)
            if response.status_code not in (429, 529) and response.status_code < 500:
                break
            if attempt == MAX_ATTEMPTS - 1:
                break
            self.retries += 1
            time.sleep(float(response.headers.get("retry-after") or 2 ** attempt))
        response.raise_for_status()
        payload = response.json()
        row = {"key": key, "served_model": payload["model"], "input_tokens": payload["usage"]["input_tokens"],
               "nouls": {name: answer["noul"] for name, answer in payload["answers"].items()}}
        self._cache[key] = row
        with self._cache_path.open("a", encoding="utf-8") as lines:
            lines.write(json.dumps(row) + "\n")
        return {**row, "seconds": time.perf_counter() - started}


def _order(scores: list[float]) -> list[int]:
    # Python's sort is stable, so equal scores keep the hybrid search order.
    return sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)


def jev_pair(client: JevClient, query: str, texts: list[str]) -> tuple[list[float], int, float]:
    """One call for each candidate, as in TypeSafe's re-ranking cookbook."""
    question = {"relevant": {"type": "noul", "instructions": PAIR_QUESTION}}
    with concurrent.futures.ThreadPoolExecutor(PARALLEL_CALLS) as pool:
        answers = list(pool.map(lambda text: client.ask({"query": query, "passage": text}, question), texts))
    return ([a["nouls"]["relevant"] for a in answers], sum(a["input_tokens"] for a in answers),
            # The candidates run in parallel: the batch takes about as long as its slowest call.
            max(a["seconds"] for a in answers))


def jev_batch(client: JevClient, query: str, texts: list[str]) -> tuple[list[float], int, float]:
    """One call for all candidates, which sends the query only once."""
    keys = [f"p{i}" for i in range(len(texts))]
    answer = client.ask({"query": query, "passages": dict(zip(keys, texts, strict=True))},
                        {k: {"type": "noul", "instructions": BATCH_QUESTION.format(key=k)} for k in keys})
    return [answer["nouls"][k] for k in keys], answer["input_tokens"], answer["seconds"]


def _rank(ids: list[str], wanted: str) -> int | None:
    return ids.index(wanted) + 1 if wanted in ids else None


def adaptive_k(scores: list[float], min_k: int) -> int:
    """How many chunks the product keeps for these scores; the chunks themselves do not affect the cut."""
    return _adaptive_k_largest_gap([(None, scores[i]) for i in _order(scores)], min_k=min_k)


def measure(db: Session, questions: list[dict], variants: list[str], client: JevClient | None) -> dict:
    ranks = {v: [] for v in variants}
    seconds = {v: [] for v in variants}
    tokens = dict.fromkeys(variants, 0)
    kept = {v: {f: [] for f in ADAPTIVE_FLOORS} for v in variants}
    adaptive_hits = {v: dict.fromkeys(ADAPTIVE_FLOORS, 0) for v in variants}
    for number, question in enumerate(questions, start=1):
        query = question["cau_hoi"]
        candidates = hybrid_search(db, uuid.UUID(question["course_id"]), query, top_k=CANDIDATES, candidate_k=CANDIDATES)
        ids = [str(c.id) for c in candidates]
        texts = [c.content for c in candidates]
        for variant in variants:
            if variant == "none":
                # Decreasing as RRF does, the same stand-in the product uses when Jev fails.
                scores, used, spent = [1 / (60 + i) for i in range(len(ids))], 0, 0.0
            elif variant == "jina":
                started = time.perf_counter()
                scored = rerank_scored(query, candidates)
                by_chunk = {id(c): score for c, score in scored}
                scores, used, spent = [by_chunk[id(c)] for c in candidates], 0, time.perf_counter() - started
            else:
                scores, used, spent = (jev_pair if variant == "jev-pair" else jev_batch)(client, query, texts)
            rank = _rank([ids[i] for i in _order(scores)], question["chunk_id"])
            ranks[variant].append(rank)
            for min_k in ADAPTIVE_FLOORS:
                k = adaptive_k(scores, min_k)
                kept[variant][min_k].append(k)
                adaptive_hits[variant][min_k] += bool(rank and rank <= k)
            tokens[variant] += used
            if spent:
                seconds[variant].append(spent)
        print(f"{number}/{len(questions)}", flush=True)
    return {v: {
        # _chi_so reads ranks from a list of ten, and here there are more candidates: MRR@10 and nDCG@10 must end at ten.
        **{name: round(value, 4) for name, value in _chi_so([r if r and r <= 10 else None for r in ranks[v]]).items()},
        **{f"adaptive_min{f}_hit": round(adaptive_hits[v][f] / len(questions), 4) for f in ADAPTIVE_FLOORS},
        **{f"adaptive_min{f}_mean_k": round(statistics.fmean(kept[v][f]), 2) for f in ADAPTIVE_FLOORS},
        "input_tokens": tokens[v],
        "cost_usd": round(tokens[v] * PRICE_PER_INPUT_TOKEN, 4),
        "mean_seconds": round(statistics.fmean(seconds[v]), 3) if seconds[v] else None,
        "p95_seconds": round(sorted(seconds[v])[round(0.95 * (len(seconds[v]) - 1))], 3) if seconds[v] else None,
        "against_jina_recall@1": _mcnemar(*[[r == 1 for r in ranks[x]] for x in ("jina", v)])
        if "jina" in ranks and v != "jina" else None,
        "ranks": ranks[v],
    } for v in variants}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("questions")
    parser.add_argument("output")
    parser.add_argument("--subset", choices=["dev", "test", "all"], required=True)
    parser.add_argument("--variants", nargs="+", choices=["none", "jina", "jev-pair", "jev-batch"], required=True)
    parser.add_argument("--cache", default="jev_cache.jsonl")
    args = parser.parse_args()
    client = None
    if any(v.startswith("jev") for v in args.variants):
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            sys.exit("TYPESAFE_API_KEY is missing")
        client = JevClient(key, Path(args.cache))
    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    chosen = [q for i, q in enumerate(questions) if in_subset(i, args.subset)]
    with Session(create_engine(os.environ["BENCHMARK_DATABASE_URL"])) as db:
        result = measure(db, chosen, args.variants, client)
    Path(args.output).write_text(json.dumps({"model": MODEL, "subset": args.subset, "questions": len(chosen),
                                             "candidates": CANDIDATES, "jev_retries": client.retries if client else 0,
                                             "results": result}, indent=2), encoding="utf-8")
    print("jev retries", client.retries if client else 0)
    for variant, row in result.items():
        print(variant, {k: v for k, v in row.items() if k != "ranks"})


if __name__ == "__main__":
    main()
