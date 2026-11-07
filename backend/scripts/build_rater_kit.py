"""Bộ tài liệu cho người chấm từ thư mục gói nghiên cứu (StudyFile JSON do export_study_pair.py ghi).

    PYTHONPATH=. uv run python scripts/build_rater_kit.py <thư mục gói> --out <thư mục bộ chấm> --key <tệp khóa.csv>

Mỗi gói thành một trang HTML tự chứa, đặt tên theo mã ẩn danh M-xxxxxxxx: đúng mã mà run_judge.py
gán cho tệp gói đó (anon_code), nên CSV của người chấm ghép thẳng được với kết quả của mô hình
chấm trong analyze_study.py. Trang chỉ có nội dung gói (packet), không có nhóm, mã cặp, tên tệp,
tên môn hay tên model. Thêm vào bộ: hướng dẫn chấm (mục 2 đến 5 của docs/rubric.md) và tệp
ratings.csv mẫu, các mã đã xáo theo `--seed`.

Tệp khóa (mã ẩn danh, mã cặp, nhóm) chỉ dành cho người nghiên cứu, nên phải nằm ngoài thư mục bộ chấm.
"""

import argparse
import csv
import html
import random
import re
import sys
from pathlib import Path

import bleach
import markdown

sys.path.insert(0, str(Path(__file__).parent))
from run_judge import _load, anon_code

RUBRIC = Path(__file__).resolve().parents[2] / "docs" / "rubric.md"
CRITERIA = ["accuracy", "completeness", "consistency", "hallucination_score", "slide_quality",
            "quiz_quality", "bloom_fidelity", "query_alignment"]
ALLOWED_TAGS = sorted(set(bleach.sanitizer.ALLOWED_TAGS) | {
    "p", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "th", "td", "hr", "br",
    "sup", "sub", "img",
})

STYLE = """
body { font: 16px/1.55 system-ui, sans-serif; max-width: 920px; margin: 0 auto; padding: 16px; color: #1d1d1f; background: #fff; }
h1 { font-size: 1.5rem; } h2 { margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: 4px; }
.source { background: #f6f6f4; border-left: 3px solid #999; padding: 8px 12px; margin: 10px 0; white-space: pre-wrap; }
.card { border: 1px solid #ddd; border-radius: 6px; padding: 10px 14px; margin: 10px 0; }
.correct { font-weight: 600; } .note { color: #555; font-size: .92rem; }
pre { overflow-x: auto; background: #f6f6f4; padding: 8px; } table { border-collapse: collapse; } td, th { border: 1px solid #ccc; padding: 4px 8px; }
@media (prefers-color-scheme: dark) { body { background: #161616; color: #e8e8e8; }
  .source, pre { background: #222; } .card, h2, td, th { border-color: #444; } .note { color: #aaa; } }
"""


def _md(text: str) -> str:
    rendered = markdown.markdown(text or "", extensions=["fenced_code", "tables"])
    return bleach.clean(rendered, tags=ALLOWED_TAGS, attributes={"img": ["alt"]}, strip=True)


def _esc(text: str) -> str:
    return html.escape(text or "")


def _question(number: int, q) -> str:
    choices = "".join(
        f'<li class="{"correct" if i == q.correct_index else ""}">{_esc(c)}{" ✓" if i == q.correct_index else ""}</li>'
        for i, c in enumerate(q.choices)
    )
    return (f'<div class="card"><p><b>Câu {number}.</b> {_esc(q.stem)}</p><ol type="A">{choices}</ol>'
            f'<p class="note"><b>Giải thích:</b> {_esc(q.explanation)}</p></div>')


def packet_page(code: str, packet) -> str:
    sources = "".join(f'<div class="source"><b>[{i}]</b> {_esc(s)}</div>'
                      for i, s in enumerate(packet.source_chunks, start=1))
    subs = "".join(f"<li>{_esc(s)}</li>" for s in packet.sub_objectives)
    slides = "".join(
        f'<div class="card"><b>Slide {i}. {_esc(s.title)}</b><ul>{"".join(f"<li>{_esc(b)}</li>" for b in s.bullets)}</ul></div>'
        for i, s in enumerate(packet.slides, start=1)
    )
    examples = "".join(
        f'<div class="card"><p><b>Ví dụ {i}.</b> {_esc(e.context)}</p><p class="note"><b>Phân tích:</b> {_esc(e.analysis)}</p></div>'
        for i, e in enumerate(packet.examples, start=1)
    )
    practice = _question(1, packet.practice)
    quiz = "".join(_question(i, q) for i, q in enumerate(packet.quiz, start=1))
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{code}</title><style>{STYLE}</style></head><body>
<h1>Gói {code}</h1>
<p><b>Chủ đề:</b> {_esc(packet.topic)}</p>
<h2>1. Đoạn tài liệu nguồn ({len(packet.source_chunks)})</h2>
<p class="note">Đối chiếu accuracy và hallucination với các đoạn này, không với hiểu biết riêng.</p>
{sources}
<h2>2. Mục tiêu học tập</h2>
<p>{_esc(packet.learning_objective)}</p><ul>{subs}</ul>
<h2>3. Nội dung</h2>
{_md(packet.content_markdown)}
<h2>4. Slide ({len(packet.slides)})</h2>
{slides}
<h2>5. Ví dụ ({len(packet.examples)})</h2>
{examples}
<h2>6. Bài luyện tập</h2>
{practice}
<h2>7. Quiz ({len(packet.quiz)}), kèm đáp án và giải thích</h2>
{quiz}
<h2>Dùng cho tiêu chí bloom_fidelity</h2>
<p>Mức Bloom khai báo: <b>{_esc(packet.bloom_level)}</b></p>
</body></html>
"""


def rubric_page() -> str:
    text = RUBRIC.read_text(encoding="utf-8")
    # Sections 2 to 5: scale, criteria, Bloom anchors and rater instructions. Section 1 names the
    # groups and the judge models, which raters must not see.
    match = re.search(r"^## 2\..*?(?=^## 6\.)", text, flags=re.MULTILINE | re.DOTALL)
    if match is None:
        raise SystemExit("Không tìm thấy mục 2 đến 5 trong docs/rubric.md")
    body = re.sub(r"\]\((?!http)[^)]*\)", "]", match.group(0))  # links into the repo do not resolve here
    return (f'<!doctype html><html lang="vi"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>Rubric</title>'
            f"<style>{STYLE}</style></head><body><h1>Rubric chấm module</h1>{_md(body)}</body></html>")


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("packets", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)
    if args.key.resolve().is_relative_to(args.out.resolve()):
        raise SystemExit("--key phải nằm ngoài thư mục bộ chấm")

    studies = [(anon_code(path), study) for path, study in _load(args.packets)]
    if not studies:
        raise SystemExit(f"Không có gói nào trong {args.packets}")
    random.Random(args.seed).shuffle(studies)

    args.out.mkdir(parents=True, exist_ok=True)
    for code, study in studies:
        (args.out / f"{code}.html").write_text(packet_page(code, study.packet), encoding="utf-8")
    (args.out / "rubric.html").write_text(rubric_page(), encoding="utf-8")
    with (args.out / "ratings.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["anon_id", *CRITERIA, "hallucination_notes"])
        writer.writerows([code, *[""] * (len(CRITERIA) + 1)] for code, _ in studies)
    order = "\n".join(f"{i}. {code}.html" for i, (code, _) in enumerate(studies, start=1))
    (args.out / "README.txt").write_text(
        "Đọc rubric.html trước, rồi chấm các gói theo thứ tự dưới đây, mỗi gói độc lập.\n"
        "Ghi điểm vào ratings.csv (một dòng mỗi gói, điểm nguyên 1-5, để trống nếu không chấm được),\n"
        "đổi tên thành rater-<tên bạn>.csv rồi gửi lại.\n\n" + order + "\n",
        encoding="utf-8",
    )

    args.key.parent.mkdir(parents=True, exist_ok=True)
    with args.key.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["anon_id", "pair_id", "group"])
        writer.writerows(sorted((code, s.pair_id, s.group) for code, s in studies))
    print(f"{len(studies)} gói -> {args.out}; khóa -> {args.key}")


if __name__ == "__main__":
    main()
