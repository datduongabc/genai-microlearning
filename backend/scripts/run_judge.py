"""Chấm mỗi gói bằng hai hãng khác nhau và khác hãng sinh.

    PYTHONPATH=. uv run python scripts/run_judge.py <folder> --out <ratings.jsonl>

Ưu tiên Claude, OpenAI, Gemini; khi hết quota hoặc gói vượt ngân sách thử các
hãng Groq đã ghim. Hai gói của một cặp dùng cùng hai alias chấm. Ghi alias và
model thực sự phục vụ từng lượt. Chạy lại giữ điểm đã xong, chỉ gọi cho phần
thiếu. Cặp lỗi hoặc hết ứng viên được báo cuối lượt; các cặp sau vẫn được chấm.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from app.generation.evaluation import judge_module
from app.generation.study_packets import StudyFile
from app.llm import gateway
from app.llm.catalog import fetch_model_info
from app.llm.judge import refuse_same_family
from app.study.judge import judge_pair


def anon_code(path: Path) -> str:
    """Mã ẩn danh của một gói: cố định theo tên tệp để chạy lại và người chấm dùng cùng một mã, nhưng
    không đọc ra được nhóm hay cặp."""
    return "M-" + hashlib.sha256(path.stem.encode("utf-8")).hexdigest()[:8]


def _load(folder: Path) -> list[tuple[Path, StudyFile]]:
    paths = sorted(folder.glob("*.json"))
    if not paths:
        raise SystemExit(f"Không có tệp gói .json nào trong {folder}")
    return [(p, StudyFile.model_validate_json(p.read_text(encoding="utf-8"))) for p in paths]


def _refuse_same_family(judge: str, studies: list[tuple[Path, StudyFile]]) -> str:
    """Trả về model thật của mô hình chấm; dừng chương trình nếu nó cùng hãng với model đã sinh gói nào."""
    try:
        return refuse_same_family(judge, [
            (path.name, study.generator_model, study.generation_models) for path, study in studies
        ], fetch_model_info())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _already_judged(out: Path) -> set[tuple[str, str]]:
    """Các cặp (mã ẩn danh, mô hình chấm) đã có trong tệp kết quả. Dòng cuối dở dang do lượt trước bị
    ngắt giữa lúc ghi (có thể cắt đôi một ký tự tiếng Việt) bị cắt bỏ khỏi tệp để chấm lại."""
    if not out.exists():
        return set()
    *complete, tail = out.read_text(encoding="utf-8", errors="replace").split("\n")
    if tail.strip():
        try:
            json.loads(tail)
            complete.append(tail)
        except json.JSONDecodeError:
            pass
        out.write_text("".join(line + "\n" for line in complete if line.strip()), encoding="utf-8")
    rows = [json.loads(line) for line in complete if line.strip()]
    return {(row["anon_id"], row["judge"]) for row in rows}


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("packets", type=Path, help="thư mục các tệp gói .json")
    parser.add_argument("--out", type=Path, required=True, help="tệp .jsonl ghi kết quả")
    args = parser.parse_args(argv)

    studies = _load(args.packets)
    catalog = fetch_model_info()
    done = _already_judged(args.out)  # Also repairs an interrupted final JSONL write before resuming.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pairs: dict[str, list[tuple[Path, StudyFile]]] = {}
    for path, study in studies:
        pairs.setdefault(study.pair_id, []).append((path, study))
    pending = []
    with args.out.open("a", encoding="utf-8") as out, gateway.experiment_mode():
        for members in pairs.values():
            by_code = {anon_code(path): (path, study) for path, study in members}
            packets = {code: study.packet.model_copy(update={"anon_id": code}) for code, (_, study) in by_code.items()}
            rated = {code: {judge for anon_id, judge in done if anon_id == code} for code in by_code}
            generators = [(path.name, study.generator_model, study.generation_models)
                          for path, study in members if study.group != "human"]

            def rate(code, packet, candidate):
                path, study = by_code[code]
                alias, expected = candidate["alias"], candidate["model"]
                with gateway.using_model(alias), gateway.capture_models() as calls:
                    evaluation = judge_module(packet, max_retries=0)
                if evaluation.anon_id != code:
                    raise ValueError("Judge returned a different anonymous item code")
                if len(calls) != 1 or calls[0]["served_model"].split("/")[-1] != expected.split("/")[-1]:
                    raise ValueError("Gateway did not report the pinned judge model")
                served = calls[0]["served_model"]
                line = {"anon_id": code, "pair_id": study.pair_id, "group": study.group,
                        "judge": alias, "judge_model": served, "evaluation": evaluation.model_dump()}
                out.write(json.dumps(line, ensure_ascii=False) + "\n")
                out.flush()
                print(f"{path.name} -> {code}: {alias} ({served})")

            codes = ", ".join(by_code)
            try:
                missing, errors = judge_pair(packets, generators, catalog, rated, rate)
            except Exception as exc:
                pending.append(f"{codes}: rating pending: {str(exc)[:200]} (rerun to resume)")
                continue
            for error in errors:
                print(f"{codes}: {error}", file=sys.stderr)
            if missing:
                pending.append(f"{codes}: missing {missing}/2 ratings (rerun to resume)")
    if pending:
        raise SystemExit("\n".join(pending))


if __name__ == "__main__":
    main()
