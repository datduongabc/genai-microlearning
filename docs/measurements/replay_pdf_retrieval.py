"""Replay the existing retrieval benchmark on all available PDF subjects.

Use the benchmark's ingestion, rankings and metrics with fixed file/page labels.
Archived queries name chunk UUIDs in an old database, so they cannot label a new
reader's chunks. This small diagnostic set is hand-labelled, not a quality study.
Run from the repository root; all databases/services must be private to this task.
"""

import argparse
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_pdf_reader import baseline_module, pdf_subjects

# Fixed page-labelled prose probes for the 2026-10-26 corpus. PDF page numbers are one-based.
PROBES = {
    "calculus 1": [
        ("1. Giới hạn dãy số.pdf", 4, "Dãy số là ánh xạ từ tập hợp nào đến tập hợp nào?"),
        ("2. Giới hạn và liên tục hàm một biến.pdf", 8, "Hai dãy cùng hội tụ về a nhưng giá trị hàm tiến tới hai giới hạn khác nhau cho biết điều gì?"),
        ("3. Đạo hàm và vi phân.pdf", 8, "Vì sao hàm giá trị tuyệt đối không có đạo hàm tại không?"),
        ("4. Tích phân xác định.pdf", 4, "Tổng tích phân được xây dựng bằng cách chia đoạn và chọn điểm như thế nào?"),
        ("5. Tích phân bất định.pdf", 4, "Hàm F được gọi là nguyên hàm của f khi thỏa điều kiện nào?"),
        ("6. Tích phân suy rộng.pdf", 4, "Tích phân suy rộng hội tụ của hàm không âm có ý nghĩa hình học gì?"),
    ],
    "operating-system": [
        ("Chapter 2_Operating System Structures.pdf", 4, "What operating system services ensure efficient resource sharing, protection and security?"),
        ("Chapter 3_Processes.pdf", 8, "What are the five process states and what does each state mean?"),
        ("Chapter 4_Threads & Concurency.pdf", 8, "How does concurrency differ from parallelism on a single processor?"),
        ("Chapter 5_CPU Scheduling.pdf", 8, "What is the difference between turnaround time and waiting time in CPU scheduling?"),
        ("Chapter 5_CPU Scheduling.pdf", 20, "How does the time quantum affect round robin scheduling and context switches?"),
        ("Chapter 6_Synchronization Tools.pdf", 8, "How can concurrent fork calls create a race condition on next_available_pid?"),
    ],
}


def load_benchmark():
    spec = importlib.util.spec_from_file_location("benchmark_rerank", ROOT / "backend/scripts/benchmark_rerank.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pages(locator):
    return {span["page"] for span in locator.get("spans", [locator]) if "page" in span}


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--queries", type=Path, help="Override the fixed current-corpus prose probes")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reader-ref", help="Git reader snapshot; omit for the worktree")
    parser.add_argument("--ocr-container", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    benchmark = load_benchmark()
    subjects = pdf_subjects(args.documents)
    benchmark.MON = dict.fromkeys(subjects, None)  # [:None] indexes every file, not the old capped sample.
    benchmark.GOC_TAI_LIEU = args.documents
    args.cache.mkdir(parents=True, exist_ok=True)
    if args.reader_ref:
        layout = baseline_module(args.reader_ref, "backend/app/ingestion/pdf_layout.py")
        reader = baseline_module(args.reader_ref, "backend/app/ingestion/document_readers.py")
        reader.page_lines, reader.title_candidates = layout.page_lines, layout.title_candidates
        benchmark.read_document = reader.read_document
    import subprocess

    from app.ingestion import document_readers

    def ocr(image, lang):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        cached = args.cache / (hashlib.sha256(lang.encode() + b"\0" + data).hexdigest() + ".txt")
        if not cached.exists():
            text = subprocess.run(["docker", "exec", "-i", args.ocr_container, "tesseract",
                                   "stdin", "stdout", "-l", lang], input=data, capture_output=True,
                                  check=True, timeout=120).stdout.decode()
            cached.write_text(text)
        return cached.read_text()

    document_readers.pytesseract.image_to_string = ocr
    # Identical input/model pairs reuse actual model vectors, including across the two runs.
    # No substitute embeddings or reranker scores are used.
    embed = benchmark.embed_texts
    vector_file = args.cache / "vectors.json"
    vectors = json.loads(vector_file.read_text()) if vector_file.exists() else {}

    def cached_embed(texts):
        keys = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
        missing = {key: text for key, text in zip(keys, texts, strict=True) if key not in vectors}
        if missing:
            values = embed(list(missing.values()))
            vectors.update(zip(missing, values, strict=True))
            vector_file.write_text(json.dumps(vectors))
        return [vectors[key] for key in keys]

    benchmark.embed_texts = cached_embed
    queries = json.loads(args.queries.read_text()) if args.queries else [
        {"subject": subject, "file": filename, "page": page, "query": query}
        for subject in subjects for filename, page, query in PROBES[subject]
    ]
    detail = []
    with benchmark._phien() as db:
        benchmark.nap(db, subjects)
        courses = {c.name: c for c in db.query(benchmark.Course).filter(benchmark.Course.name.in_(subjects))}
        chunks = {name: db.query(benchmark.Chunk).filter(benchmark.Chunk.course_id == course.id).all()
                  for name, course in courses.items()}
        documents = {d.id: d.filename for d in db.query(benchmark.Document)}
        for query in queries:
            course = courses[query["subject"]]
            gold = {c.id for c in chunks[course.name]
                    if documents[c.document_id] == query["file"] and query["page"] in pages(json.loads(c.source_ref))}
            if not gold:
                raise ValueError(f"No source chunk for {query}")
            ranks = {}
            for mode in benchmark.CAU_HINH:
                ids = benchmark._xep_hang(db, course.id, query["query"], mode)
                ranks[mode] = next((i + 1 for i, ident in enumerate(ids) if ident in gold), None)
            detail.append({**query, "ranks": ranks, "gold_chunks": len(gold)})
            print(course.name, query["query"], ranks, flush=True)
        metrics = {mode: {subject: benchmark._chi_so([d["ranks"][mode] for d in detail
                                                    if d["subject"] == subject]) for subject in subjects}
                   for mode in benchmark.CAU_HINH}
        for mode, metric in metrics.items():
            metric["all"] = benchmark._chi_so([d["ranks"][mode] for d in detail])
        result = {"reader_ref": args.reader_ref or "worktree", "queries": len(queries),
                  "chunk_counts": {name: len(items) for name, items in chunks.items()},
                  "embedding_model": benchmark.settings.embedding_model,
                  "reranker_model": "BAAI/bge-reranker-base@2cfc18c9415c912f9d8155881c133215df768a70",
                  "metrics": metrics, "detail": detail}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
