"""Chat answers for the 50 questions of chat-eval/, from the eight contexts stored in answers-gemini-fast-v2.jsonl.

    docker exec -w /app -e PYTHONPATH=. do-an-backend /app/.venv/bin/python /tmp/replay_answers.py \\
        --contexts /tmp/fm/answers-gemini-fast-v2.jsonl --alias claude-fast --out /tmp/fm/answers-claude-fast.jsonl

The measurement database of 2026-11-07 no longer exists, and the dev database was rechunked since, so the
questions' source chunks are gone. The eight contexts Jina rerank picked for each question in v2 are kept in
the answer file, and only their text reaches the prompt (format_source_chunks), so replaying them gives the
chat prompt of v2 exactly, with the same retrieval for every model. Then the same call as chat_eval.py:
answer_question, one turn, no history, pinned to one alias in experiment mode. Resumed like chat_eval.py.
"""

import argparse
import json
import time
import uuid
from pathlib import Path

from app.chat.service import answer_question
from app.llm.gateway import capture_models, experiment_mode, using_model, waiting_on_rate_limits
from app.models import Chunk


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contexts", type=Path, required=True)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.contexts.read_text(encoding="utf-8").splitlines() if line.strip()]
    done = set()
    if args.out.exists():
        done = {json.loads(line)["chunk_id"] for line in args.out.read_text(encoding="utf-8").splitlines() if line.strip()}
    with args.out.open("a", encoding="utf-8") as sink:
        for number, row in enumerate(rows, 1):
            if row["chunk_id"] in done:
                continue
            contexts = [Chunk(id=uuid.UUID(i), content=t) for i, t in zip(row["context_ids"], row["contexts"], strict=True)]
            started = time.perf_counter()
            with experiment_mode(), using_model(args.alias), waiting_on_rate_limits(), capture_models() as calls:
                answer = answer_question(row["question"], contexts, [])
            seconds = time.perf_counter() - started
            record = {
                "chunk_id": row["chunk_id"], "course": row["course"], "question": row["question"],
                "answer": answer.answer, "contexts": row["contexts"], "context_ids": row["context_ids"],
                "cited_ids": [c.chunk_id for c in answer.citations], "source_in_contexts": row["source_in_contexts"],
                "reranked": row["reranked"], "alias": args.alias,
                "served_models": sorted({c["served_model"] for c in calls}), "seconds": round(seconds, 2),
            }
            sink.write(json.dumps(record, ensure_ascii=False) + "\n")
            sink.flush()
            print(f"{number}/{len(rows)} {row['course']}: {seconds:.1f}s, cited {len(record['cited_ids'])}, "
                  f"models {record['served_models']}", flush=True)


if __name__ == "__main__":
    main()
