"""Phân tích kết quả chấm của nghiên cứu RAG so với không RAG.

    uv run python scripts/analyze_study.py <judge_a.jsonl> [<judge_b.jsonl>] [--human <ratings.csv>] [--out <report.json>]

Đầu vào là các tệp do scripts/run_judge.py ghi, mỗi mô hình chấm một tệp, và tuỳ chọn một tệp CSV điểm
của người chấm (cột anon_id cùng các cột tiêu chí, xem docs/rubric.md). Với từng mô hình chấm:

- điểm trung bình mỗi tiêu chí của từng nhóm;
- Wilcoxon signed-rank hai phía trên các cặp cùng chủ đề (rag_llm trừ llm_no_rag) cho từng tiêu chí,
  kèm p đã hiệu chỉnh Holm cho tám tiêu chí. Các cặp chênh 0 bị loại theo cách của Wilcoxon;
- McNemar chính xác (kiểm nhị thức trên các cặp bất đồng) cho "có ít nhất một hallucination".

Giữa hai mô hình chấm, và giữa mỗi mô hình chấm với người: Spearman và kappa trọng số bậc hai trên
thang 1-5, ghép theo mã ẩn danh. Một chỉ số không xác định (ví dụ mọi điểm bằng nhau) ghi là null.
"""

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from scipy import stats

from app.generation.evaluation import DimensionScore, ModuleEvaluation

# Tám tiêu chí chấm điểm, theo đúng thứ tự của rubric. Tiêu chí thứ chín là danh sách
# hallucination_findings, được phân tích bằng McNemar chứ không bằng điểm.
CRITERIA = [name for name, field in ModuleEvaluation.model_fields.items() if field.annotation is DimensionScore]
SCALE = range(1, 6)


@dataclass(frozen=True)
class Judged:
    anon_id: str
    pair_id: str
    group: str
    scores: dict[str, int]
    hallucinated: bool


def load_judgements(path: str) -> list[Judged]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        evaluation = ModuleEvaluation.model_validate(raw["evaluation"])
        rows.append(Judged(
            anon_id=raw["anon_id"], pair_id=raw["pair_id"], group=raw["group"],
            scores={c: getattr(evaluation, c).score for c in CRITERIA},
            hallucinated=bool(evaluation.hallucination_findings),
        ))
    return rows


def _finite(value: float) -> float | None:
    return None if value is None or math.isnan(value) else float(value)


def group_means(rows: list[Judged]) -> dict[str, dict[str, float]]:
    means: dict[str, dict[str, float]] = {}
    for group in sorted({r.group for r in rows}):
        members = [r for r in rows if r.group == group]
        means[group] = {c: sum(r.scores[c] for r in members) / len(members) for c in CRITERIA}
    return means


def pairs(rows: list[Judged]) -> list[tuple[Judged, Judged]]:
    """(rag_llm, llm_no_rag) cùng mã cặp. Gói thiếu bạn cặp không vào được phép kiểm ghép cặp."""
    by_group = {g: {r.pair_id: r for r in rows if r.group == g} for g in ("rag_llm", "llm_no_rag")}
    common = sorted(by_group["rag_llm"].keys() & by_group["llm_no_rag"].keys())
    return [(by_group["rag_llm"][p], by_group["llm_no_rag"][p]) for p in common]


def holm(p_values: list[float]) -> list[float]:
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted = [0.0] * len(p_values)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


def compare_groups(rows: list[Judged]) -> dict[str, dict]:
    matched = pairs(rows)
    result = {}
    for c in CRITERIA:
        diffs = [rag.scores[c] - no_rag.scores[c] for rag, no_rag in matched]
        if any(diffs):
            test = stats.wilcoxon(diffs)
            statistic, p = float(test.statistic), float(test.pvalue)
        else:
            # Mọi cặp bằng nhau: không có bằng chứng nào về khác biệt; scipy không tính được.
            statistic, p = 0.0, 1.0
        result[c] = {
            "n": len(diffs), "mean_diff": sum(diffs) / len(diffs) if diffs else None,
            "statistic": statistic, "p": p,
        }
    for c, adjusted in zip(CRITERIA, holm([result[c]["p"] for c in CRITERIA]), strict=True):
        result[c]["p_holm"] = adjusted
    return result


def mcnemar_hallucination(rows: list[Judged]) -> dict:
    matched = pairs(rows)
    rag_only = sum(1 for rag, no_rag in matched if rag.hallucinated and not no_rag.hallucinated)
    no_rag_only = sum(1 for rag, no_rag in matched if no_rag.hallucinated and not rag.hallucinated)
    discordant = rag_only + no_rag_only
    p = stats.binomtest(rag_only, discordant, 0.5).pvalue if discordant else 1.0
    return {
        "n": len(matched), "rag_only": rag_only, "no_rag_only": no_rag_only,
        "rag_rate": sum(r.hallucinated for r, _ in matched) / len(matched) if matched else None,
        "no_rag_rate": sum(n.hallucinated for _, n in matched) / len(matched) if matched else None,
        "p": float(p),
    }


def quadratic_weighted_kappa(first: list[int], second: list[int]) -> float | None:
    """Kappa trọng số bậc hai trên đủ năm mức của thang, kể cả mức không ai dùng tới."""
    k, n = len(SCALE), len(first)
    index = {level: i for i, level in enumerate(SCALE)}
    observed = [[0.0] * k for _ in range(k)]
    for a, b in zip(first, second, strict=True):
        observed[index[a]][index[b]] += 1
    rows = [sum(observed[i]) for i in range(k)]
    cols = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    weight = [[(i - j) ** 2 / (k - 1) ** 2 for j in range(k)] for i in range(k)]
    seen = sum(weight[i][j] * observed[i][j] for i in range(k) for j in range(k)) / n
    expected = sum(weight[i][j] * rows[i] * cols[j] for i in range(k) for j in range(k)) / n**2
    return None if expected == 0 else 1 - seen / expected


def scores_by_code(rows: list[Judged]) -> dict[str, dict[str, int]]:
    return {r.anon_id: r.scores for r in rows}


def agreement(first: dict[str, dict[str, int]], second: dict[str, dict[str, int]]) -> dict[str, dict]:
    """Chỉ số đồng thuận cho từng tiêu chí mà cả hai bên cùng chấm, ghép theo mã ẩn danh."""
    result = {}
    for c in CRITERIA:
        codes = sorted(code for code in first.keys() & second.keys() if c in first[code] and c in second[code])
        if not codes:
            continue
        a = [first[code][c] for code in codes]
        b = [second[code][c] for code in codes]
        spearman = stats.spearmanr(a, b).statistic if len(codes) > 1 and len(set(a)) > 1 and len(set(b)) > 1 else None
        result[c] = {"n": len(codes), "spearman": _finite(spearman), "kappa": quadratic_weighted_kappa(a, b)}
    return result


def load_human(path: str) -> dict[str, dict[str, int]]:
    """CSV của người chấm: cột anon_id và một cột cho mỗi tiêu chí đã chấm (bỏ trống thì không tính)."""
    ratings: dict[str, dict[str, int]] = {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            code = (row.get("anon_id") or "").strip()
            if not code:
                raise SystemExit(f"{path}:{line}: thiếu anon_id")
            if code in ratings:
                raise SystemExit(f"{path}:{line}: anon_id {code} xuất hiện hai lần; mỗi tệp chỉ một người chấm")
            scores = {}
            for c in CRITERIA:
                value = (row.get(c) or "").strip()
                if not value:
                    continue
                if not value.isdigit() or int(value) not in SCALE:
                    raise SystemExit(f"{path}:{line}: {c} = {value!r}, phải là số nguyên từ 1 đến 5")
                scores[c] = int(value)
            ratings[code] = scores
    return ratings


def _print_report(report: dict) -> None:
    def fmt(value) -> str:
        return "-" if value is None else f"{value:.3f}"

    for name, judge in report["judges"].items():
        means = judge["means"]
        print(f"\n== Mô hình chấm: {name}")
        print(f"{'tiêu chí':<22}{'rag_llm':>9}{'no_rag':>9}{'chênh':>8}{'n':>4}{'p':>8}{'p Holm':>8}")
        for c in CRITERIA:
            w = judge["wilcoxon"][c]
            print(
                f"{c:<22}{fmt(means.get('rag_llm', {}).get(c)):>9}{fmt(means.get('llm_no_rag', {}).get(c)):>9}"
                f"{fmt(w['mean_diff']):>8}{w['n']:>4}{fmt(w['p']):>8}{fmt(w['p_holm']):>8}"
            )
        m = judge["mcnemar"]
        print(
            f"Có hallucination: rag_llm {fmt(m['rag_rate'])}, llm_no_rag {fmt(m['no_rag_rate'])}; "
            f"chỉ RAG {m['rag_only']}, chỉ không RAG {m['no_rag_only']}, McNemar p = {fmt(m['p'])} (n = {m['n']})"
        )
    for name, per_criterion in report["agreement"].items():
        print(f"\n== Đồng thuận {name}")
        print(f"{'tiêu chí':<22}{'n':>4}{'Spearman':>10}{'kappa':>8}")
        for c, a in per_criterion.items():
            print(f"{c:<22}{a['n']:>4}{fmt(a['spearman']):>10}{fmt(a['kappa']):>8}")


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("judges", nargs="+", help="một hoặc hai tệp .jsonl của scripts/run_judge.py")
    parser.add_argument("--human", help="CSV điểm của người chấm")
    parser.add_argument("--out", type=Path, help="ghi thêm báo cáo dạng JSON vào tệp này")
    args = parser.parse_args(argv)
    if len(args.judges) > 2:
        parser.error("tối đa hai tệp mô hình chấm")

    loaded = {Path(p).stem: load_judgements(p) for p in args.judges}
    if len(loaded) != len(args.judges):
        parser.error("hai tệp mô hình chấm phải có tên khác nhau")
    report = {"judges": {}, "agreement": {}}
    for name, rows in loaded.items():
        report["judges"][name] = {
            "means": group_means(rows), "wilcoxon": compare_groups(rows), "mcnemar": mcnemar_hallucination(rows),
        }
    names = list(loaded)
    if len(names) == 2:
        report["agreement"][f"{names[0]}~{names[1]}"] = agreement(
            scores_by_code(loaded[names[0]]), scores_by_code(loaded[names[1]])
        )
    if args.human:
        human = load_human(args.human)
        for name in names:
            report["agreement"][f"{name}~human"] = agreement(scores_by_code(loaded[name]), human)

    _print_report(report)
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
