"""Formula re-read of the flagged PDF pages with one vision alias, page by page.

Selects pages exactly as read_pdf does (text layer long enough, suspect_formula_lines on the
math lines, rendered at 150 dpi), then calls repair_page with FORMULA_VISION_MODEL set to
--alias. Prints one JSON row per page: accepted or kept with a formula_unverified warning,
seconds, tokens, cost and the served model. --dry only lists the flagged pages.

    docker exec -w /app -e PYTHONPATH=. do-an-backend /app/.venv/bin/python /tmp/fm/formula_reread.py \\
        --alias deepseek-flash --pdf "/tmp/fm/1. Giới hạn dãy số.pdf" --out /tmp/fm/deepseek-flash.jsonl
"""
import argparse
import json
import time
from pathlib import Path

import pymupdf

from app import timing
from app.config import settings
from app.ingestion import formula_repair
from app.ingestion.document_readers import MIN_TEXT_LEN_PER_PAGE, _render_dpi
from app.ingestion.pdf_layout import page_lines, suspect_formula_lines
from app.llm.gateway import capture_models, experiment_mode


def flagged_pages(path: str) -> list[tuple[int, str, bytes]]:
    doc = pymupdf.open(path)
    pages = []
    for index, page in enumerate(doc):
        code_lines, math_lines = set(), set()
        lines = page_lines(page, code_lines=code_lines, math_lines=math_lines)
        text = "\n".join(lines)
        if len(text.strip()) < MIN_TEXT_LEN_PER_PAGE:
            continue
        if suspect_formula_lines([lines[i] for i in sorted(math_lines)]):
            pages.append((index + 1, text, page.get_pixmap(dpi=_render_dpi(page.rect, 150)).tobytes("png")))
    doc.close()
    return pages[: settings.formula_vision_max_pages]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias")
    parser.add_argument("--pdf", action="append", required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    settings.formula_vision_model = args.alias
    out = args.out.open("w", encoding="utf-8") if args.out else None
    for path in args.pdf:
        pages = flagged_pages(path)
        print(Path(path).name, "flagged", [p for p, _, _ in pages], flush=True)
        if args.dry:
            continue
        for number, text, png in pages:
            row = {"alias": args.alias, "file": Path(path).name, "page": number}
            start = time.monotonic()
            with experiment_mode(), timing.capture_timings() as spans, capture_models() as served:
                try:
                    result = formula_repair.repair_page(png, text)
                    row["error"] = None
                except Exception as error:  # the reader keeps the page with a warning on any model error
                    result, row["error"] = None, f"{type(error).__name__}: {error}"[:300]
            calls = [s for s in spans if s["kind"] == "llm"]
            row |= {
                "accepted": result is not None, "seconds": round(time.monotonic() - start, 2),
                "prompt_tokens": sum(s.get("prompt_tokens") or 0 for s in calls),
                "completion_tokens": sum(s.get("completion_tokens") or 0 for s in calls),
                "cost_usd": sum(s.get("cost_usd") or 0 for s in calls),
                "served_models": sorted({c["served_model"] for c in served}),
                "transcription": result,
            }
            print(json.dumps({k: v for k, v in row.items() if k != "transcription"}, ensure_ascii=False), flush=True)
            if out:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()


if __name__ == "__main__":
    main()
