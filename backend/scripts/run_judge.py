"""Chấm một thư mục gói nghiên cứu bằng LLM chấm theo rubric chín tiêu chí (docs/rubric.md).

    PYTHONPATH=. uv run python scripts/run_judge.py <thư mục gói> --judge <alias> --out <tệp .jsonl>

Thư mục là đầu ra của scripts/export_study_pair.py, mỗi tệp một StudyFile. Mỗi gói ra một dòng JSON
trong tệp kết quả: mã ẩn danh, mã cặp, nhóm, mô hình chấm và bản chấm đầy đủ. scripts/analyze_study.py
đọc tệp này.

Mô hình chấm phải khác hãng với mô hình đã sinh gói, vì mô hình chấm thường ưu ái văn của chính họ nhà
mình (self-preference bias). Hãng là nơi làm ra model chứ không phải nơi phục vụ nó: gpt-oss chạy trên
Groq vẫn là model của OpenAI. Kiểm cho mọi gói trước khi gọi lượt nào, để một thư mục lẫn gói không
hợp lệ không tốn tiền nửa chừng rồi mới dừng.

Chạy lại với cùng tệp kết quả thì chỉ chấm những gói mô hình chấm này còn thiếu: lượt chấm bị
ngắt giữa chừng (hết hạn mức, mất mạng) không phải gọi lại cho phần đã xong. Mô hình chấm khác ghi vào
cùng tệp thì chấm đủ mọi gói; scripts/analyze_study.py tách các dòng theo mô hình chấm.

Tốn một lượt gọi LLM thật cho mỗi gói, qua gateway; điều kiện chạy miễn phí ở docs/rubric.md, mục 6.
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
    parser.add_argument("--judge", required=True, help="alias gateway của mô hình chấm, ví dụ claude-pro")
    parser.add_argument("--out", type=Path, required=True, help="tệp .jsonl ghi kết quả")
    args = parser.parse_args(argv)

    studies = _load(args.packets)
    judge_model = _refuse_same_family(args.judge, studies)
    done = _already_judged(args.out)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as out, gateway.experiment_mode(), gateway.using_model(args.judge):
        for path, study in studies:
            code = anon_code(path)
            if (code, args.judge) in done:
                continue
            evaluation = judge_module(study.packet.model_copy(update={"anon_id": code}))
            line = {
                "anon_id": code, "pair_id": study.pair_id, "group": study.group,
                "judge": args.judge, "judge_model": judge_model, "evaluation": evaluation.model_dump(),
            }
            # Ghi và đẩy xuống đĩa từng dòng: bị ngắt thì những gói đã chấm vẫn còn.
            out.write(json.dumps(line, ensure_ascii=False) + "\n")
            out.flush()
            print(f"{path.name} -> {code}")


if __name__ == "__main__":
    main()
