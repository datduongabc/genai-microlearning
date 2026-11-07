"""Course chat answers for the Ragas evaluation (faithfulness, answer relevance), on a fixed question set.

    PYTHONPATH=. python scripts/chat_eval.py select --questions ../docs/measurements/truy_van_5mon.json \\
        --out ../docs/measurements/chat-eval/questions.json
    PYTHONPATH=. python scripts/chat_eval.py answer --questions ../docs/measurements/chat-eval/questions.json \\
        --alias gemini-fast --out ../docs/measurements/chat-eval/answers-gemini-fast.jsonl

`select` takes every fifth question of each subject from the retrieval set (250 questions, one source
chunk each, docs/measurements/2026-11-04-five-subject-baseline.md): 10 per subject, 50 in all. It stores
the source chunk's text with each question, so the set does not depend on the database it came from.

`answer` asks each question the way POST /courses/{id}/chat does (search_for_chat, rerank_for_chat,
answer_question; one turn, no history), pinned to one alias with the gateway's fallbacks off, and
writes one JSON line per question: the answer, the eight context chunks the model was given, the
chunks it cited, the model the gateway actually served, whether the reranker answered, and whether
the source chunk was among the contexts. A run that stops is resumed: questions already in the
output file are skipped. Run it with DATABASE_URL, the owner's URL: it reads every course.
"""

import argparse
import json
import logging
import time
import uuid
from pathlib import Path

from sqlalchemy import select

from app.chat.service import answer_question
from app.db import SessionLocal
from app.llm.gateway import capture_models, experiment_mode, using_model, waiting_on_rate_limits
from app.models import Chunk, Course, Document
from app.retrieval.search import rerank_for_chat, search_for_chat

PER_SUBJECT = 10
STEP = 5


class _RerankFallback(logging.Handler):
    """Counts the chat's own warning when the reranker fails and the hybrid order is kept."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.count = 0

    def emit(self, record):
        if "chat keeps the hybrid order" in record.getMessage():
            self.count += 1


def select_questions(source: Path, out: Path) -> None:
    rows = json.loads(source.read_text(encoding="utf-8"))
    by_subject: dict[str, list[dict]] = {}
    for row in rows:
        by_subject.setdefault(row["course"], []).append(row)
    chosen = [row for subject in sorted(by_subject) for row in by_subject[subject][::STEP][:PER_SUBJECT]]
    with SessionLocal() as db:
        for row in chosen:
            chunk = db.get(Chunk, uuid.UUID(row["chunk_id"]))
            document = db.get(Document, chunk.document_id)
            row.update(reference=chunk.content, source_ref=chunk.source_ref or "", document=document.filename)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(chosen, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(chosen)} questions from {len(by_subject)} subjects -> {out}")


def answer_all(questions_file: Path, alias: str, out: Path, pause: float) -> None:
    questions = json.loads(questions_file.read_text(encoding="utf-8"))
    done = set()
    if out.exists():
        done = {json.loads(line)["chunk_id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()}
    fallback = _RerankFallback()
    logging.getLogger("app.retrieval.search").addHandler(fallback)
    with SessionLocal() as db, out.open("a", encoding="utf-8") as sink:
        for number, row in enumerate(questions, 1):
            if row["chunk_id"] in done:
                continue
            course = db.scalar(select(Course).where(Course.id == uuid.UUID(row["course_id"])))
            before = fallback.count
            started = time.perf_counter()
            with experiment_mode(), using_model(alias), waiting_on_rate_limits(), capture_models() as calls:
                contexts = rerank_for_chat(row["cau_hoi"], search_for_chat(db, course.id, row["cau_hoi"]), top_k=8)
                answer = answer_question(row["cau_hoi"], contexts, [])
            seconds = time.perf_counter() - started
            record = {
                "chunk_id": row["chunk_id"], "course": row["course"], "question": row["cau_hoi"],
                "answer": answer.answer, "contexts": [c.content for c in contexts],
                "context_ids": [str(c.id) for c in contexts], "cited_ids": [c.chunk_id for c in answer.citations],
                "source_in_contexts": row["chunk_id"] in {str(c.id) for c in contexts},
                "reranked": fallback.count == before, "alias": alias,
                "served_models": sorted({c["served_model"] for c in calls}), "seconds": round(seconds, 2),
            }
            sink.write(json.dumps(record, ensure_ascii=False) + "\n")
            sink.flush()
            print(f"{number}/{len(questions)} {row['course']}: {seconds:.1f}s, cited {len(record['cited_ids'])}, "
                  f"source {'in' if record['source_in_contexts'] else 'NOT in'} contexts, models {record['served_models']}")
            time.sleep(pause)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    pick = sub.add_parser("select")
    pick.add_argument("--questions", type=Path, required=True)
    pick.add_argument("--out", type=Path, required=True)
    ask = sub.add_parser("answer")
    ask.add_argument("--questions", type=Path, required=True)
    ask.add_argument("--alias", required=True)
    ask.add_argument("--out", type=Path, required=True)
    # A free tier allows about 15 requests a minute; one question is one call.
    ask.add_argument("--pause", type=float, default=4.5)
    args = parser.parse_args()
    if args.command == "select":
        select_questions(args.questions, args.out)
    else:
        answer_all(args.questions, args.alias, args.out, args.pause)


if __name__ == "__main__":
    main()
