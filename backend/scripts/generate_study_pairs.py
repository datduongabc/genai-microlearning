"""Sinh module RAG cho từng tài liệu rồi xuất cặp gói nghiên cứu (rag_llm, llm_no_rag) của nó.

    PYTHONPATH=. uv run python scripts/generate_study_pairs.py <document_id>... --model <alias> --out <thư mục>

Mỗi tài liệu: nếu chưa có module RAG sinh bằng đúng alias này thì sinh một module (chế độ thí
nghiệm, không fallback), rồi gọi export_study_pair.py để sinh bản không RAG cùng Learning
Objective và ghi hai tệp StudyFile. Chạy lại thì dùng lại module đã có, nên một lượt bị ngắt
giữa chừng có thể chạy tiếp. Gặp giới hạn số lượt gọi (429) thì chờ rồi thử lại; module chỉ được
lưu khi mọi lượt gọi đã xong, nên một lần thử hỏng không để lại module dở.
"""

import argparse
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.generation.module_generation_pipeline import is_control, run_module_generation
from app.llm import gateway
from app.models import Document, Module

sys.path.insert(0, str(Path(__file__).parent))
import export_study_pair

RATE_LIMIT_WAIT_SECONDS = 65
ATTEMPTS = 5


def _rate_limited(error: BaseException) -> bool:
    while error is not None:
        if "RateLimit" in type(error).__name__ or " 429" in str(error) or "RESOURCE_EXHAUSTED" in str(error):
            return True
        error = error.__cause__ or error.__context__
    return False


def rag_module(db, doc: Document, alias: str) -> Module | None:
    for module in db.scalars(select(Module).where(Module.document_id == doc.id).order_by(Module.created_at)):
        payload = module.generation_payload or {}
        if payload.get("model_alias") == alias and not is_control(db, module.id):
            return module
    return None


def generate(doc_id: uuid.UUID, alias: str) -> uuid.UUID:
    for attempt in range(1, ATTEMPTS + 1):
        db = SessionLocal()
        try:
            doc = db.get(Document, doc_id)
            if doc is None or not doc.raw_text:
                raise SystemExit(f"Tài liệu {doc_id} không có hoặc chưa có raw_text")
            existing = rag_module(db, doc, alias)
            if existing is not None:
                return existing.id
            started = time.perf_counter()
            with gateway.experiment_mode(), gateway.using_model(alias):
                module = run_module_generation(db, doc)
            print(f"  RAG module {module.id} ({time.perf_counter() - started:.0f}s)", flush=True)
            return module.id
        except Exception as error:
            if not _rate_limited(error) or attempt == ATTEMPTS:
                raise
            print(f"  429, chờ {RATE_LIMIT_WAIT_SECONDS}s (lần {attempt})", flush=True)
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
        finally:
            db.close()
    raise AssertionError("unreachable")


def export(module_id: uuid.UUID, out: Path) -> None:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            export_study_pair.main([str(module_id), "--out", str(out)])
            return
        except Exception as error:
            if not _rate_limited(error) or attempt == ATTEMPTS:
                raise
            print(f"  429, chờ {RATE_LIMIT_WAIT_SECONDS}s (lần {attempt})", flush=True)
            time.sleep(RATE_LIMIT_WAIT_SECONDS)


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("document_ids", nargs="+", type=uuid.UUID)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    for doc_id in args.document_ids:
        print(f"Tài liệu {doc_id}", flush=True)
        module_id = generate(doc_id, args.model)
        if all((args.out / f"{module_id}-{group}.json").exists() for group in ("rag_llm", "llm_no_rag")):
            print("  đã xuất", flush=True)
            continue
        started = time.perf_counter()
        export(module_id, args.out)
        print(f"  xuất cặp ({time.perf_counter() - started:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
