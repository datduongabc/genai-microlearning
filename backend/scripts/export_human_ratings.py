"""Xuất điểm chấm mù của người chấm trong một nghiên cứu ra CSV, mỗi người một tệp.

    PYTHONPATH=. uv run python scripts/export_human_ratings.py <study_id> --out study/human

Người chấm chấm trong ứng dụng (bảng ratings, rater_kind = 'human'), còn analyze_study.py đọc mỗi
người chấm một CSV đúng định dạng của docs/rubric.md mục 5. Tệp được đặt tên ẩn danh rater-1,
rater-2, ... theo thứ tự email, để luận văn không lộ ai đã chấm gì; bảng đối chiếu tên tệp với email
chỉ in ra màn hình cho người nghiên cứu.

Rồi phân tích cả nhóm, gồm độ tin cậy giữa các người chấm (Krippendorff, Fleiss):

    PYTHONPATH=. uv run python scripts/analyze_study.py study/rubric.jsonl \\
        --human study/human/rater-1.csv --human study/human/rater-2.csv
"""

import argparse
import csv
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Rating, StudyItem, User
from scripts.analyze_study import CRITERIA


def _notes(findings: list[dict] | None) -> str:
    return "\n".join(f"{f.get('field', '')}: {f.get('claim', '')} ({f.get('reason', '')})" for f in findings or [])


def export(study_id: uuid.UUID, out: Path) -> list[Path]:
    """Ghi một CSV cho mỗi người đã chấm ít nhất một gói của nghiên cứu; trả về các tệp đã ghi."""
    with SessionLocal() as db:
        rows = db.execute(
            select(User.email, StudyItem.anon_code, Rating)
            .join(StudyItem, Rating.study_item_id == StudyItem.id)
            .join(User, Rating.user_id == User.id)
            .where(StudyItem.study_id == study_id, Rating.rater_kind == "human")
            .order_by(User.email, StudyItem.anon_code)
        ).all()
    by_email: dict[str, list[tuple[str, Rating]]] = {}
    for email, code, rating in rows:
        by_email.setdefault(email, []).append((code, rating))

    out.mkdir(parents=True, exist_ok=True)
    written = []
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
    parser.add_argument("--out", type=Path, required=True, help="thư mục ghi các tệp rater-N.csv")
    args = parser.parse_args(argv)
    if not export(args.study_id, args.out):
        sys.exit("Chưa có người chấm nào chấm gói nào của nghiên cứu này.")


if __name__ == "__main__":
    main()
