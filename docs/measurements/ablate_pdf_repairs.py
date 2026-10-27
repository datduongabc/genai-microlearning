"""Measure what disabling each formula or VNI repair changes against the pinned original reader.

Run from the repository root. Each ablation disables one repair only; the combined
ablation disables all five. This tests contribution on this corpus, not other PDFs.
"""

import argparse
import json
from pathlib import Path

import pymupdf
from measure_pdf_reader import baseline_module, changed_chars, pdf_subjects, stats


def disable(module, feature):
    if feature in ("vertical_bars", "combined"):
        module._absolute_bars = lambda *args: []
    if feature in ("large_brackets", "combined"):
        module._merge_brackets = lambda pieces: pieces
    if feature in ("limits", "combined"):
        module._takes_limits = lambda text: False
        module._is_big_operator = lambda text: False
    if feature in ("vni", "combined"):
        module._vni_fonts = lambda blocks: set()
    if feature in ("radicals", "combined"):
        original = module._strokes

        def strokes(page):
            rules, verticals = original(page)
            for rule in rules:
                rule.radical = False
            return rules, verticals

        module._strokes = strokes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", required=True, type=Path)
    parser.add_argument("--baseline-ref", default="760483b9825d646d785e78ef3c56989998142088")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    filename = "backend/app/ingestion/pdf_layout.py"
    legacy = baseline_module(args.baseline_ref, filename)
    variants = {}
    for feature in ("vertical_bars", "large_brackets", "limits", "vni", "radicals", "combined"):
        variant = baseline_module(args.baseline_ref, filename)
        disable(variant, feature)
        variants[feature] = variant
    results = []
    for subject in pdf_subjects(args.documents):
        paths = sorted((args.documents / subject).glob("*.pdf"))
        if not paths:
            raise ValueError(f"No PDFs for {subject}")
        for path in paths:
            row = {"file": f"docs/documents/{subject}/{path.name}", "pages": 0, "ablations": {}}
            with pymupdf.open(path) as doc:
                row["pages"] = len(doc)
                for page in doc:
                    before = legacy.page_lines(page)
                    for feature, module in variants.items():
                        after = module.page_lines(page)
                        if before != after:
                            row["ablations"].setdefault(feature, []).append({
                                "page": page.number + 1,
                                "characters_changed": changed_chars("\n".join(before), "\n".join(after)),
                                "fragment_delta": stats(after)["fragments"] - stats(before)["fragments"],
                                "before": "\n".join(before), "after": "\n".join(after),
                            })
            results.append(row)
            print(row["file"], {k: len(v) for k, v in row["ablations"].items()}, flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"baseline_ref": args.baseline_ref, "files": results},
                                      ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
