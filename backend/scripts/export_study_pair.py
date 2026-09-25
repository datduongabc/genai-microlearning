"""Xuất cặp gói nghiên cứu (rag_llm, llm_no_rag) của một module RAG ra hai tệp JSON.

    uv run python scripts/export_study_pair.py <module_id> --out <thư mục> [--model <alias>]

Bản không RAG được sinh bằng đúng model đã sinh module RAG (đọc từ job sinh module), để khác biệt
điểm chấm chỉ còn là RAG; --model chỉ dùng khi muốn ghi đè có chủ ý. Phân hệ nghiên cứu nạp hai tệp
này và gán mã ẩn danh; tên tệp mang tên nhóm để người nghiên cứu biết, còn nội dung gói thì không.

Tốn lượt gọi LLM thật (khoảng 25-30 lượt cho bản không RAG), và một lượt nhúng câu truy vấn.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

from app.db import SessionLocal
from app.generation.study_packets import study_pair
from app.models import Job, JobType, Module


def model_that_made(db, module_id: uuid.UUID) -> str | None:
    job = (
        db.query(Job)
        .filter(Job.module_id == module_id, Job.job_type == JobType.module_generation)
        .order_by(Job.created_at.desc())
        .first()
    )
    # None nghĩa là module được sinh bằng model mặc định trong cấu hình, và bản đối chứng cũng vậy.
    return (job.payload or {}).get("model_alias") if job is not None else None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("module_id", type=uuid.UUID)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default=None, help="ghi đè model của bản không RAG")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        module = db.get(Module, args.module_id)
        if module is None:
            raise SystemExit(f"Không có module {args.module_id}")
        alias = args.model or model_that_made(db, module.id)
        rag, no_rag = study_pair(db, module, alias)
    finally:
        db.close()

    args.out.mkdir(parents=True, exist_ok=True)
    for group, packet in (("rag_llm", rag), ("llm_no_rag", no_rag)):
        path = args.out / f"{args.module_id}-{group}.json"
        path.write_text(json.dumps(packet.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
