"""Xuất điểm chấm mù của một nghiên cứu trong ứng dụng: người chấm ra CSV, mô hình chấm ra judge.jsonl.

    PYTHONPATH=. uv run python scripts/export_human_ratings.py <study_id> --out study/inapp

Người chấm chấm trong ứng dụng (bảng ratings, rater_kind = 'human'), còn analyze_study.py đọc mỗi
người chấm một CSV đúng định dạng của docs/rubric.md mục 5. Tệp được đặt tên ẩn danh rater-1,
rater-2, ... theo thứ tự email, để luận văn không lộ ai đã chấm gì; bảng đối chiếu tên tệp với email
chỉ in ra màn hình cho người nghiên cứu.

Điểm của mô hình chấm trong ứng dụng (rater_kind = 'llm_judge') ghi vào judge.jsonl, đúng dạng dòng của
run_judge.py và cùng mã ẩn danh M01, M02, ... với các CSV, nên ghép được với người chấm. Gói rag_llm
được ghép cặp với gói llm_no_rag có cùng Learning Objective và mục tiêu con (bản đối chứng dùng lại LO
của module RAG); gói không có cặp mang mã cặp là chính mã ẩn danh của nó.

Rồi phân tích cả nhóm, gồm độ tin cậy giữa các người chấm (Krippendorff, Fleiss):

    PYTHONPATH=. uv run python scripts/analyze_study.py study/inapp/judge.jsonl \\
        --human study/inapp/rater-1.csv --human study/inapp/rater-2.csv
"""

import argparse
import csv
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from app.api.studies import _rating_out
from app.db import SessionLocal
from app.models import Rating, StudyItem, User
from scripts.analyze_study import CRITERIA


def _notes(findings: list[dict] | None) -> str:
    return "\n".join(f"{f.get('field', '')}: {f.get('claim', '')} ({f.get('reason', '')})" for f in findings or [])


def _pair_ids(items: list[StudyItem]) -> dict[str, str]:
    """Mã cặp theo mã ẩn danh: mỗi gói rag_llm với đúng một gói llm_no_rag cùng LO và mục tiêu con."""
    by_objective: dict[tuple, dict[str, list[StudyItem]]] = {}
    for item in items:
        key = (item.packet.get("learning_objective"), tuple(item.packet.get("sub_objectives") or []))
        by_objective.setdefault(key, {}).setdefault(item.group, []).append(item)
    pair_ids = {item.anon_code: item.anon_code for item in items}
    for groups in by_objective.values():
        rag, no_rag = groups.get("rag_llm", []), groups.get("llm_no_rag", [])
        if len(rag) == 1 and len(no_rag) == 1:
            pair_ids[no_rag[0].anon_code] = rag[0].anon_code
    return pair_ids


def _export_judge(db, study_id: uuid.UUID, out: Path) -> Path | None:
    rows = db.execute(
        select(StudyItem, Rating)
        .join(Rating, Rating.study_item_id == StudyItem.id)
        .where(StudyItem.study_id == study_id, Rating.rater_kind == "llm_judge")
        .order_by(StudyItem.anon_code, Rating.judge_alias)
    ).all()
    if not rows:
        return None
    pair_ids = _pair_ids(db.scalars(select(StudyItem).where(StudyItem.study_id == study_id)).all())
    path = out / "judge.jsonl"
    with open(path, "w", encoding="utf-8") as handle:
        for item, rating in rows:
            line = {
                "anon_id": item.anon_code, "pair_id": pair_ids[item.anon_code], "group": item.group,
                "judge": rating.judge_alias, "judge_model": rating.judge_model,
                "evaluation": _rating_out(rating, item.anon_code),
            }
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    print(f"{path.name}: {len(rows)} lượt chấm của mô hình chấm")
    return path


def export(study_id: uuid.UUID, out: Path) -> list[Path]:
    """Ghi một CSV cho mỗi người đã chấm ít nhất một gói của nghiên cứu và judge.jsonl cho các lượt chấm
    của mô hình chấm; trả về các tệp đã ghi."""
    existing = sorted(p.name for p in [*out.glob("rater-*.csv"), *out.glob("judge.jsonl")])
    if existing:
        sys.exit(
            f"Thư mục {out} đã có {', '.join(existing)}. Hãy xoá các tệp này hoặc chọn thư mục khác, "
            "để tệp cũ không bị phân tích như một người chấm thêm."
        )
    with SessionLocal() as db:
        rows = db.execute(
            select(User.email, StudyItem.anon_code, Rating)
            .join(StudyItem, Rating.study_item_id == StudyItem.id)
            .join(User, Rating.user_id == User.id)
            .where(StudyItem.study_id == study_id, Rating.rater_kind == "human")
            .order_by(User.email, StudyItem.anon_code)
        ).all()
        out.mkdir(parents=True, exist_ok=True)
        judge = _export_judge(db, study_id, out)
    by_email: dict[str, list[tuple[str, Rating]]] = {}
    for email, code, rating in rows:
        by_email.setdefault(email, []).append((code, rating))

    written = [judge] if judge else []
    for n, (email, ratings) in enumerate(sorted(by_email.items()), start=1):
        path = out / f"rater-{n}.csv"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["anon_id", *CRITERIA, "hallucination_notes"])
            for code, rating in ratings:
                writer.writerow([
                    code, *(getattr(rating, c)["score"] for c in CRITERIA), _notes(rating.hallucination_findings),
                ])
        print(f"{path.name}: {email} ({len(ratings)} gói)")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("study_id", type=uuid.UUID)
    parser.add_argument("--out", type=Path, required=True, help="thư mục ghi các tệp rater-N.csv và judge.jsonl")
    args = parser.parse_args(argv)
    if not export(args.study_id, args.out):
        sys.exit("Chưa có người chấm hay mô hình chấm nào chấm gói nào của nghiên cứu này.")


if __name__ == "__main__":
    main()
