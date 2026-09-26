"""Xuất cặp gói nghiên cứu (rag_llm, llm_no_rag) của một module RAG ra hai tệp JSON.

    PYTHONPATH=. uv run python scripts/export_study_pair.py <module_id> --out <thư mục> [--model <alias>]

Bản không RAG được sinh bằng đúng model đã sinh module RAG (đọc từ job sinh module), để khác biệt
điểm chấm chỉ còn là RAG; --model chỉ dùng khi muốn ghi đè có chủ ý. Mỗi tệp là một StudyFile: gói
nội dung (khóa "packet", không lộ nhóm hay nguồn) bọc trong mã cặp, tên nhóm và model đã sinh. Phần
bọc ngoài chỉ dành cho người nghiên cứu và scripts/run_judge.py, không đưa cho người chấm.

Tốn lượt gọi LLM thật (12 lượt trên đường thuận cho bản không RAG, 7 khi module RAG tắt tự kiểm), và
một lượt nhúng câu truy vấn.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

from app.db import SessionLocal
from app.generation.study_packets import StudyFile, study_pair
from app.llm import gateway
from app.models import Job, JobType, Module


def _generation_payload(db, module_id: uuid.UUID) -> dict:
    job = (
        db.query(Job)
        .filter(Job.module_id == module_id, Job.job_type == JobType.module_generation)
        .order_by(Job.created_at.desc())
        .first()
    )
    return (job.payload or {}) if job is not None else {}


def model_that_made(db, module_id: uuid.UUID) -> str | None:
    # None nghĩa là module được sinh bằng model mặc định trong cấu hình, và bản đối chứng cũng vậy.
    return _generation_payload(db, module_id).get("model_alias")


def self_check_of(db, module_id: uuid.UUID) -> bool:
    """Module RAG có được sinh với vòng tự kiểm không; job cũ không ghi cờ này là có, như mặc định."""
    return _generation_payload(db, module_id).get("self_check", True)


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("module_id", type=uuid.UUID)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default=None, help="ghi đè model của bản không RAG")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        module = db.get(Module, args.module_id)
        if module is None:
            raise SystemExit(f"Không có module {args.module_id}")
        rag_alias = model_that_made(db, module.id)
        no_rag_alias = args.model or rag_alias
        rag, no_rag = study_pair(db, module, no_rag_alias, self_check_of(db, module.id))
    finally:
        db.close()

    # Ghi tên alias thật chứ không để trống: bước chấm cần biết hãng của model đã sinh từng gói.
    args.out.mkdir(parents=True, exist_ok=True)
    for group, packet, alias in (("rag_llm", rag, rag_alias), ("llm_no_rag", no_rag, no_rag_alias)):
        generator = alias or gateway.active_model()
        study = StudyFile(pair_id=str(args.module_id), group=group, generator_model=generator, packet=packet)
        path = args.out / f"{args.module_id}-{group}.json"
        path.write_text(json.dumps(study.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
