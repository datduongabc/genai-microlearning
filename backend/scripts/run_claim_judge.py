"""Separate R9 assessment of Content claims and stored RAG citations.

PYTHONPATH=. uv run python scripts/run_claim_judge.py study/packets --judge <verified-free-alias> --out study/claims.jsonl

No R8 rubric calls. Same cross-family guard and interrupted-line recovery as run_judge.
A changed packet cannot silently reuse an old assessment: use a new output file.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from app.generation.claim_evaluation import assess_content
from app.llm import gateway
from scripts.run_judge import _already_judged, _load, _refuse_same_family, anon_code


def fingerprint(study) -> str:
    return hashlib.sha256(study.model_dump_json().encode()).hexdigest()


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("packets", type=Path)
    parser.add_argument("--judge", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    studies = _load(args.packets)
    # Compare the reference snapshot, not the citations chosen by the RAG generator.
    pairs = {}
    for _, study in studies:
        evidence = (study.packet.source_chunks, study.reference.model_dump())
        if study.pair_id in pairs and pairs[study.pair_id] != evidence:
            raise SystemExit(f"{study.pair_id}: paired groups must have identical reference evidence")
        pairs[study.pair_id] = evidence
    judge_model = _refuse_same_family(args.judge, studies)
    done = _already_judged(args.out)
    if args.out.exists():
        current = {anon_code(path): fingerprint(study) for path, study in studies}
        for line in args.out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["anon_id"] in current and row.get("packet_sha256") != current[row["anon_id"]]:
                raise SystemExit("Packet changed or output is not R9; use a new output file")
            if row["judge"] == args.judge and row.get("judge_model") != judge_model:
                raise SystemExit("Judge model changed; use a new output file")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as out, gateway.using_model(args.judge):
        for path, study in studies:
            code = anon_code(path)
            if (code, args.judge) in done:
                continue
            evaluation = assess_content(
                study.packet.content_markdown, study.packet.source_chunks, study.reference,
                study.claim_citations if study.group == "rag_llm" else None,
            )
            row = {
                "anon_id": code, "pair_id": study.pair_id, "group": study.group,
                "judge": args.judge, "judge_model": judge_model, "packet_sha256": fingerprint(study),
                "claim_evaluation": evaluation.model_dump(),
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print(f"{path.name} -> {code}: unsupported_rate={evaluation.unsupported_rate}")


if __name__ == "__main__":
    main()
