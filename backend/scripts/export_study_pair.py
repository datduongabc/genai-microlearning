"""Xuất cặp gói nghiên cứu (rag_llm, llm_no_rag) của một module RAG ra hai tệp JSON.

    PYTHONPATH=. uv run python scripts/export_study_pair.py <module_id> --out <thư mục> [--model <alias>]

Bản không RAG là một module sinh bằng chính pipeline RAG với use_retrieval=False (được lưu như mọi
module khác và dùng lại ở các lần xuất sau với cùng alias), với alias yêu cầu của module RAG
(đọc từ job sinh module); --model chỉ dùng khi muốn ghi đè có chủ ý. Điều kiện ghim model và
kiểm tra model thực sự đã sinh nằm trong docs/gateway-routing.md. Mỗi tệp là một StudyFile: gói
nội dung (khóa "packet", không lộ nhóm hay nguồn) bọc trong mã cặp, tên nhóm và model đã sinh. Metadata đối chiếu và trích dẫn R9 cũng nằm ở lớp bọc ngoài; xem docs/rubric.md
cho định dạng và quy trình chấm. Không đưa toàn bộ lớp bọc cho người chấm.

Khi chưa có bản đối chứng, bước xuất gọi LLM để sinh bản không RAG. Chỉ nhúng câu truy vấn
khi bộ đối chiếu cần truy hồi; mục nhỏ dùng toàn bộ chunk sẵn có, không cần lượt nhúng này.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

from app.db import SessionLocal
from app.generation.module_generation_pipeline import generation_payload_of
from app.generation.study_packets import StudyFile, study_evidence, study_pair
from app.llm import gateway
from app.models import Module


def model_that_made(db, module_id: uuid.UUID) -> str | None:
    # Job cũ không lưu alias dùng mặc định hiện tại; không suy ra được model đã phục vụ.
    return generation_payload_of(db, module_id).get("model_alias")


def self_check_of(db, module_id: uuid.UUID) -> bool:
    """Module RAG có được sinh với vòng tự kiểm không; job cũ không ghi cờ này là có, như mặc định."""
    return generation_payload_of(db, module_id).get("self_check", True)


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
        generation_models = {}
        content_versions = {}
        try:
            with gateway.experiment_mode():
                rag, no_rag = study_pair(db, module, no_rag_alias, self_check_of(db, module.id), generation_models, content_versions)
            reference, citations = study_evidence(db, module, rag.source_chunks)
        except ValueError as loi:
            raise SystemExit(str(loi)) from loi
    finally:
        db.close()

    # Giữ alias cho gói cũ không có bản ghi model đã phục vụ.
    args.out.mkdir(parents=True, exist_ok=True)
    for group, packet, alias in (("rag_llm", rag, rag_alias), ("llm_no_rag", no_rag, no_rag_alias)):
        generator = alias or gateway.active_model()
        study = StudyFile(
            pair_id=str(args.module_id), group=group, generator_model=generator, packet=packet,
            generation_models=generation_models.get(group, []),
            content_sources=content_versions[group],
            reference=reference, claim_citations=citations if group == "rag_llm" else None,
        )
        path = args.out / f"{args.module_id}-{group}.json"
        path.write_text(json.dumps(study.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
