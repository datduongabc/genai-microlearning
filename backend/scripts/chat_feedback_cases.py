"""Chat answers readers said were wrong, kept as regression cases, and replayed against the current search.

    PYTHONPATH=. python scripts/chat_feedback_cases.py export --out ../docs/measurements/chat-regression/cases.jsonl
    PYTHONPATH=. python scripts/chat_feedback_cases.py replay --cases ../docs/measurements/chat-regression/cases.jsonl

`export` appends to the case file every answer that was marked "not right" or "wrong citation", and every
answer whose reader asked nearly the same question again within two minutes (the signal people give without
pressing anything). A case holds the question, the question as rewritten for the search, the answer, the
passages it was given with their reranker scores, the citations the reader flagged and their note. Cases
already in the file are kept and not repeated, so the file grows into a regression set. No reader's name or
id is written.

`replay` searches each case's question again with the chat's search and reranker (no model call, so it
costs only the embedding and reranker calls) and reports, per case, whether a flagged passage still comes
first, whether the passages the answer cited are still found, and the best score against the chat's
threshold. A change to chunking, search, reranking or the threshold is judged on these before it ships.
Run both with DATABASE_URL, the owner's URL: they read every course.
"""

import argparse
import json
import re
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import ChatRole, ChatSession
from app.retrieval.search import rerank_scored_for_chat, search_for_chat

ASKED_AGAIN_WITHIN = timedelta(minutes=2)
SAME_QUESTION = 0.5


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def asked_again(question: str, next_question: str) -> bool:
    """The next question shares at least half of its words with this one: the reader tried again."""
    a, b = _words(question), _words(next_question)
    return bool(a and b) and len(a & b) / len(a | b) >= SAME_QUESTION


def _cases(db) -> list[dict]:
    cases = []
    sessions = db.scalars(select(ChatSession).where(ChatSession.module_id.is_(None)))
    for session in sessions:
        messages = session.messages
        for i, message in enumerate(messages):
            if message.role != ChatRole.assistant:
                continue
            question = next((m.content for m in reversed(messages[:i]) if m.role == ChatRole.user), None)
            follow = next((m for m in messages[i + 1:] if m.role == ChatRole.user), None)
            again = (question is not None and follow is not None
                     and follow.created_at - message.created_at <= ASKED_AGAIN_WITHIN
                     and asked_again(question, follow.content))
            if message.feedback not in ("not_right", "wrong_citation") and not again:
                continue
            trace = message.trace or {}
            cases.append({
                "message_id": str(message.id),
                "course_id": str(session.course_id),
                "kind": message.feedback or "asked_again",
                "question": question,
                "rewritten": trace.get("rewritten"),
                "answer": message.content,
                "route": trace.get("route"),
                "passages": trace.get("passages", []),
                "cited": [c["chunk_id"] for c in message.citations],
                "flagged": [message.citations[n - 1]["chunk_id"] for n in message.feedback_citations
                            if 0 < n <= len(message.citations)],
                "note": message.feedback_note,
                "asked_next": follow.content if again else None,
            })
    return cases


def export(out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    known = set()
    if out.exists():
        known = {json.loads(line)["message_id"] for line in out.read_text(encoding="utf-8").splitlines() if line}
    with SessionLocal() as db:
        new = [case for case in _cases(db) if case["message_id"] not in known]
    with out.open("a", encoding="utf-8") as file:
        for case in new:
            file.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"{len(new)} new cases, {len(known) + len(new)} in {out}")


def replay(cases_path: Path) -> list[dict]:
    rows = []
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        case = json.loads(line)
        query = case["rewritten"] or case["question"]
        with SessionLocal() as db:
            candidates = search_for_chat(db, uuid.UUID(case["course_id"]), query)
        chunks, scores = rerank_scored_for_chat(query, candidates)
        found = [str(c.id) for c in chunks]
        row = {
            "message_id": case["message_id"], "kind": case["kind"],
            "flagged_still_first": bool(found) and found[0] in case["flagged"],
            "flagged_still_found": sum(f in found for f in case["flagged"]),
            "cited_still_found": sum(c in found for c in case["cited"]),
            "cited": len(case["cited"]),
            "top_score": scores[0] if scores else None,
            "below_threshold": bool(scores) and scores[0] < settings.chat_min_rerank_score,
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    print(f"{len(rows)} cases; flagged passage still first in {sum(r['flagged_still_first'] for r in rows)}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("export").add_argument("--out", type=Path, required=True)
    commands.add_parser("replay").add_argument("--cases", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export":
        export(args.out)
    else:
        replay(args.cases)


if __name__ == "__main__":
    main()
