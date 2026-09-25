"""Chấm một thư mục gói nghiên cứu bằng LLM chấm theo rubric chín tiêu chí (docs/rubric.md).

    uv run python scripts/run_judge.py <thư mục gói> --judge <alias> --out <tệp .jsonl>

Thư mục là đầu ra của scripts/export_study_pair.py, mỗi tệp một StudyFile. Mỗi gói ra một dòng JSON
trong tệp kết quả: mã ẩn danh, mã cặp, nhóm, mô hình chấm và bản chấm đầy đủ. scripts/analyze_study.py
đọc tệp này.

Mô hình chấm phải khác hãng với mô hình đã sinh gói, vì mô hình chấm thường ưu ái văn của chính họ nhà
mình (self-preference bias). Hãng là nơi làm ra model chứ không phải nơi phục vụ nó: gpt-oss chạy trên
Groq vẫn là model của OpenAI. Kiểm cho mọi gói trước khi gọi lượt nào, để một thư mục lẫn gói không
hợp lệ không tốn tiền nửa chừng rồi mới dừng.

Chạy lại với cùng tệp kết quả thì chỉ chấm những gói còn thiếu: lượt chấm trả tiền bị ngắt giữa chừng
(hết hạn mức, mất mạng) không phải trả lại cho phần đã xong.

Tốn một lượt gọi LLM thật cho mỗi gói, qua gateway.
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

# Hãng theo tiền tố tên model. Không đọc từ tiền tố nhà cung cấp của litellm, vì Groq phục vụ model
# của nhiều hãng khác nhau.
_FAMILY_BY_NAME = (
    ("gemini", "google"),
    ("gemma", "google"),
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("llama", "meta"),
    ("qwen", "alibaba"),
)


def model_family(litellm_model: str) -> str:
    parts = litellm_model.lower().split("/")
    for prefix, family in _FAMILY_BY_NAME:
        if parts[-1].startswith(prefix):
            return family
    # "groq/<hãng>/<model>" thì đoạn giữa là hãng; còn lại lấy nhà cung cấp.
    return parts[-2] if len(parts) >= 3 else parts[0]


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
    models = {m["model_name"]: m.get("litellm_params", {}).get("model", "") for m in fetch_model_info()}
    if judge not in models:
        raise SystemExit(f"Gateway không có alias chấm '{judge}'. Các alias hiện có: {', '.join(sorted(models))}")
    judge_family = model_family(models[judge])
    for path, study in studies:
        if study.generator_model not in models:
            raise SystemExit(
                f"{path.name}: gateway không có alias '{study.generator_model}' đã sinh gói này, nên không "
                "kiểm được mô hình chấm có khác hãng hay không."
            )
        if model_family(models[study.generator_model]) == judge_family:
            raise SystemExit(
                f"Từ chối: mô hình chấm '{judge}' cùng hãng {judge_family} với model "
                f"'{study.generator_model}' đã sinh {path.name}. Chọn mô hình chấm của hãng khác."
            )
    return models[judge]


def _already_judged(out: Path) -> set[str]:
    if not out.exists():
        return set()
    return {json.loads(line)["anon_id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()}


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
    with args.out.open("a", encoding="utf-8") as out, gateway.using_model(args.judge):
        for path, study in studies:
            code = anon_code(path)
            if code in done:
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
