"""Compare every PDF of the four retained subjects with a pinned reader baseline.

Run from the repository root with backend/.venv/bin/python. Output goes to --output;
PDFs are opened read-only. Character changes count replaced/inserted/deleted characters
(maximum of the two lengths per edit) after removing whitespace, not edit distance.
"""

import argparse
import collections
import difflib
import hashlib
import io
import json
import re
import subprocess
import sys
import time
import types
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import pymupdf
from app.ingestion import document_readers, pdf_layout
from app.ingestion.chunker import chunk_units, document_title

SUBJECTS = ("dsa", "operating-system", "program-fundamentals", "web-programming")
BULLET = re.compile(r"^[\s]*[•▪◦❖➢\uf06e\uf075]|^\s*[-–*o]\s")


def baseline_module(ref, filename):
    module = types.ModuleType(f"baseline_{Path(filename).stem}")
    sys.modules[module.__name__] = module
    source = subprocess.check_output(["git", "show", f"{ref}:{filename}"], cwd=ROOT, text=True)
    exec(compile(source, filename, "exec"), module.__dict__)  # noqa: S102 -- trusted local git snapshot
    return module


def changed_chars(a, b):
    a, b = re.sub(r"\s+", "", a), re.sub(r"\s+", "", b)
    if a == b:
        return 0
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in
               difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal")


def stats(lines):
    lines = [line for line in lines if line.strip()]
    return {
        "lines": len(lines),
        "fragments": sum(len(re.sub(r"\s+", "", line)) <= 3 for line in lines),
        "characters": sum(len(line) for line in lines),
        "bullets": sum(bool(BULLET.match(line)) for line in lines),
        "diacritics": sum(bool(unicodedata.combining(c)) for c in
                          unicodedata.normalize("NFD", "\n".join(lines))),
        "explicit_indented_lines": sum(line.startswith((" ", "\t")) for line in lines),
    }


def locator_pages(locator):
    return {s["page"] for s in locator.get("spans", [locator]) if "page" in s}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--baseline-ref", default="760483b9825d646d785e78ef3c56989998142088")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--current-ref", help="Use a git reader snapshot instead of the worktree")
    parser.add_argument("--ocr-container", help="Run Tesseract eng+vie in this private container")
    parser.add_argument("--ocr-cache", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    legacy = baseline_module(args.baseline_ref, "backend/app/ingestion/pdf_layout.py")
    reader = baseline_module(args.baseline_ref, "backend/app/ingestion/document_readers.py")
    reader.page_lines, reader.title_candidates = legacy.page_lines, legacy.title_candidates
    current_layout, current_reader = pdf_layout, document_readers
    if args.current_ref:
        current_layout = baseline_module(args.current_ref, "backend/app/ingestion/pdf_layout.py")
        current_reader = baseline_module(args.current_ref, "backend/app/ingestion/document_readers.py")
        current_reader.page_lines = current_layout.page_lines
        current_reader.title_candidates = current_layout.title_candidates
    if args.ocr_container:
        def ocr(image, lang):
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            data = buffer.getvalue()
            cached = (args.ocr_cache / (hashlib.sha256(data).hexdigest() + ".txt")) if args.ocr_cache else None
            if cached and cached.exists():
                return cached.read_text()
            result = subprocess.run(["docker", "exec", "-i", args.ocr_container, "tesseract",
                                     "stdin", "stdout", "-l", lang], input=data, capture_output=True,
                                    check=True, timeout=120).stdout.decode()
            if cached:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(result)
            return result
        document_readers.pytesseract.image_to_string = ocr
    results, details = [], []
    for subject in SUBJECTS:
        paths = sorted((args.documents / subject).glob("*.pdf"))
        if not paths:
            raise ValueError(f"No PDFs for {subject}")
        for path in paths:
            row = {"file": f"docs/documents/{subject}/{path.name}",
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "pages": 0,
                   "mapping_spans": {}, "mapping_chars": {}, "sparse_pages": []}
            mapping_spans, mapping_chars = collections.Counter(), collections.Counter()
            encoded_brackets, vni_characters = 0, 0
            totals = {name: collections.Counter() for name in
                      ("plain", "legacy", "no_strokes", "lines_only", "current")}
            times = collections.Counter()
            text_changes = collections.Counter()
            preserved = collections.Counter()
            with pymupdf.open(path) as doc:
                for page in doc:
                    blocks = [b for b in page.get_text("rawdict", flags=legacy._TEXT_FLAGS)["blocks"]
                              if b["type"] == 0 and b["lines"]]
                    vni = frozenset(legacy._vni_fonts(blocks))
                    source_lines = [line for b in blocks for line in b["lines"]]
                    line_reference = [legacy._line_text(line["spans"], vni) for line in source_lines]
                    code_reference = [text for line, text in zip(source_lines, line_reference, strict=True)
                                      if any(s["flags"] & pymupdf.TEXT_FONT_MONOSPACED
                                             for s in line["spans"]) and text.strip()]
                    for line in source_lines:
                        for span in line["spans"]:
                            text = legacy._span_text(span)
                            mapped, bracket = legacy.map_font_text(text, span["font"], vni)
                            encoded_brackets += bool(bracket)
                            if span["font"].lower().startswith("vni"):
                                vni_characters += len(text)
                            if text != mapped:
                                name = span["font"].lower()
                                family = "vni" if span["font"] in vni else (
                                    "symbol" if "symbol" in name else (
                                        "dingbat" if any(f in name for f in legacy._DINGBAT_FONTS)
                                        else "bracket"))
                                mapping_spans[family] += 1
                                mapping_chars[family] += changed_chars(text, mapped)
                    outputs = {}
                    strokes = legacy._strokes
                    for name, total in totals.items():
                        elapsed = []
                        for _ in range(args.repeats):
                            start = time.perf_counter()
                            if name == "plain":
                                lines = page.get_text("text").splitlines()
                            elif name == "legacy":
                                lines = legacy.page_lines(page)
                            elif name == "no_strokes":
                                legacy._strokes = lambda p: ([], [])
                                try:
                                    lines = legacy.page_lines(page)
                                finally:
                                    legacy._strokes = strokes
                            elif name == "lines_only":
                                lines = line_reference
                            else:
                                lines = current_layout.page_lines(page)
                            elapsed.append(time.perf_counter() - start)
                        times[name] += sorted(elapsed)[len(elapsed) // 2]
                        outputs[name] = "\n".join(lines)
                        total.update(stats(lines))
                        # Ordered substring matches allow a fragment to join its neighbour,
                        # while detecting deletion or reordering of complete code lines.
                        cursor = 0
                        for code in code_reference:
                            pos = outputs[name].find(code, cursor)
                            if pos != -1:
                                preserved[f"{name}_code_lines"] += 1
                                cursor = pos + len(code)
                    preserved["source_code_lines"] += len(code_reference)
                    page_changes = {name: changed_chars(outputs["plain"], value)
                                    for name, value in outputs.items()}
                    for name, value in page_changes.items():
                        text_changes[f"plain_to_{name}_chars"] += value
                    for name in ("no_strokes", "lines_only", "current"):
                        different = outputs["legacy"] != outputs[name]
                        text_changes[f"legacy_to_{name}_pages"] += different
                        text_changes[f"legacy_to_{name}_chars"] += changed_chars(
                            outputs["legacy"], outputs[name])
                    if any(outputs["legacy"] != outputs[n] for n in ("no_strokes", "current")):
                        details.append({"file": row["file"], "page": page.number + 1,
                                        "text": outputs})
                    if len(outputs["legacy"].strip()) < 20:
                        row["sparse_pages"].append(page.number + 1)
                row["pages"] = len(doc)
            # Real reader/chunker comparison, without changing their splitting rules.
            before_units = reader.read_pdf(str(path))
            after_units = current_reader.read_pdf(str(path))
            before = chunk_units(before_units, title=document_title(path.name))
            after = chunk_units(after_units, title=document_title(path.name))
            # Chunk-index shifts make pairwise zip counts noisy. Also compare locator multisets
            # and covered source pages, and reject any invented page number.
            before_locators = collections.Counter(json.dumps(c.locator, sort_keys=True) for c in before)
            after_locators = collections.Counter(json.dumps(c.locator, sort_keys=True) for c in after)
            before_pages = set().union(*(locator_pages(c.locator) for c in before))
            after_pages = set().union(*(locator_pages(c.locator) for c in after))
            if not after_pages <= set(range(1, row["pages"] + 1)):
                raise ValueError(f"Invalid source page in {path}")
            row.update({"mapping_spans": dict(mapping_spans), "mapping_chars": dict(mapping_chars),
                        "encoded_bracket_spans": encoded_brackets,
                        "vni_font_characters": vni_characters,
                        "metrics": {name: {**dict(v), "fragment_rate": v["fragments"] / v["lines"]
                                           if v["lines"] else 0,
                                           "ms_per_page": times[name] * 1000 / row["pages"]}
                                    for name, v in totals.items()},
                        "changes": dict(text_changes), "code_preservation": dict(preserved),
                        "chunks_before": len(before), "chunks_after": len(after),
                        "warning_pages": sorted({w["page"] for u in after_units
                                                 for w in getattr(u, "warnings", [])}),
                        "chunk_page_coverage_before": sorted(before_pages),
                        "chunk_page_coverage_after": sorted(after_pages),
                        "chunk_locators_removed": sum((before_locators - after_locators).values()),
                        "chunk_locators_added": sum((after_locators - before_locators).values()),
                        "chunk_locator_changes": sum(a.locator != b.locator for a, b in
                                                     zip(before, after, strict=False)),
                        "chunk_content_changes": sum(a.content != b.content for a, b in
                                                     zip(before, after, strict=False))})
            results.append(row)
            print(row["file"], row["pages"], row["changes"], flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "pdf-reader-files.json").write_text(json.dumps({
        "baseline_ref": args.baseline_ref, "pymupdf": pymupdf.VersionBind,
        "current_ref": args.current_ref or "worktree",
        "ocr": "tesseract eng+vie" if args.ocr_container else "host pytesseract",
        "repeats": args.repeats, "files": results}, ensure_ascii=False, indent=2) + "\n")
    (args.output / "pdf-reader-page-diffs.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
