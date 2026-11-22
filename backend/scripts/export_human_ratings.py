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
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Study, StudyItem
from app.study.results import criteria_of, human_ratings, judge_jsonl, pair_ids, rater_csv


def _pair_ids(items: list[StudyItem]) -> dict[str, str]:
    """Mã cặp theo mã ẩn danh (app/study/results.py); báo những gói không ghép được."""
    pairs, unpaired = pair_ids(items)
    if unpaired:
        print(f"Cảnh báo: không ghép được cặp cho {', '.join(unpaired)} (thiếu bạn cặp hoặc nhiều gói "
              "cùng LO và mục tiêu con); các gói này bị loại khỏi phép kiểm ghép cặp.")
    return pairs


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
        humans = human_ratings(db, study_id)
        criteria = criteria_of(db.get(Study, study_id))
        _pair_ids(list(db.scalars(select(StudyItem).where(StudyItem.study_id == study_id))))
        judge = judge_jsonl(db, study_id)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    if judge:
        path = out / "judge.jsonl"
        path.write_text(judge, encoding="utf-8")
        print(f"{path.name}: {judge.count(chr(10))} lượt chấm của mô hình chấm")
        written.append(path)
    # Same numbering as the admin page: by email among raters who rated at least one packet.
    for n, (email, ratings) in enumerate(humans.items(), start=1):
        path = out / f"rater-{n}.csv"
        path.write_text(rater_csv(ratings, criteria), encoding="utf-8", newline="")
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
