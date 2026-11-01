"""Phân tích kết quả chấm của nghiên cứu RAG so với không RAG.

    PYTHONPATH=. uv run python scripts/analyze_study.py <judge.jsonl>... [--human <ratings.csv>] [--out <report.json>]

--claims <claims.jsonl>... thêm metric R9 từ run_claim_judge.py, cộng tử số/mẫu số
theo mô hình và nhóm, loại gói có bộ đối chiếu chưa đủ khỏi tỉ lệ claim không có căn cứ.

Đầu vào là các tệp do scripts/run_judge.py ghi, và tuỳ chọn một tệp CSV điểm của người chấm (cột anon_id
cùng các cột tiêu chí, xem docs/rubric.md). Các dòng được tách theo mô hình chấm ghi trong dòng, không
theo tệp: một tệp có thể chứa nhiều mô hình chấm, và một mô hình chấm có thể nằm ở nhiều tệp. Với từng
mô hình chấm:

- điểm trung bình mỗi tiêu chí của từng nhóm;
- Wilcoxon signed-rank hai phía trên các cặp cùng chủ đề (rag_llm trừ llm_no_rag) cho từng tiêu chí,
  kèm p đã hiệu chỉnh Holm cho tám tiêu chí. Các cặp chênh 0 bị loại theo cách của Wilcoxon;
- McNemar chính xác (kiểm nhị thức trên các cặp bất đồng) cho "có ít nhất một hallucination".

Giữa từng cặp mô hình chấm, và giữa mỗi mô hình chấm với người: Spearman và kappa trọng số bậc hai trên
thang 1-5, ghép theo mã ẩn danh. Một chỉ số không xác định (ví dụ mọi điểm bằng nhau) ghi là null.

Lặp --human cho mỗi người chấm (scripts/export_human_ratings.py xuất sẵn mỗi người một tệp). Từ hai
người trở lên còn có đồng thuận giữa từng cặp người và độ tin cậy của cả nhóm: Krippendorff's alpha
thứ bậc trên mọi điểm, Fleiss' kappa trên các gói ai cũng chấm, và alpha khi thêm mỗi mô hình chấm
vào nhóm người như một người chấm nữa.
"""

import argparse
import csv
import itertools
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from scipy import stats

from app.generation.claim_evaluation import ClaimEvaluation
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


def load_judgements(paths: list[str]) -> dict[str, list[Judged]]:
    """Các dòng chấm của mọi tệp, tách theo mô hình chấm. Một gói được cùng mô hình chấm chấm hai lần thì
    dừng: giữ lần nào cũng là chọn điểm một cách tuỳ tiện."""
    by_judge: dict[str, dict[str, Judged]] = {}
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            judged = by_judge.setdefault(raw["judge"], {})
            if raw["anon_id"] in judged:
                raise SystemExit(f"{path}: mô hình chấm {raw['judge']} chấm gói {raw['anon_id']} hai lần")
            evaluation = ModuleEvaluation.model_validate(raw["evaluation"])
            judged[raw["anon_id"]] = Judged(
                anon_id=raw["anon_id"], pair_id=raw["pair_id"], group=raw["group"],
                scores={c: getattr(evaluation, c).score for c in CRITERIA},
                hallucinated=bool(evaluation.hallucination_findings),
            )
    return {judge: list(rows.values()) for judge, rows in by_judge.items()}


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


def _squared_difference(metric: str, levels: list[int], totals: dict[int, int]):
    """delta^2 giữa hai giá trị theo loại thang của Krippendorff (2011)."""
    if metric == "nominal":
        return lambda c, k: 0.0 if c == k else 1.0
    if metric == "interval":
        return lambda c, k: float(c - k) ** 2
    if metric == "ordinal":
        position = {level: i for i, level in enumerate(levels)}

        def ordinal(c, k):
            low, high = sorted((position[c], position[k]))
            between = sum(totals.get(levels[g], 0) for g in range(low, high + 1))
            return (between - (totals.get(c, 0) + totals.get(k, 0)) / 2) ** 2

        return ordinal
    raise ValueError(f"unknown metric {metric!r}")


def krippendorff_alpha(units: list[list[int]], metric: str = "ordinal", levels=SCALE) -> float | None:
    """Alpha của Krippendorff qua ma trận trùng khớp: mỗi đơn vị là danh sách điểm mà các người chấm
    đã cho nó (thiếu người nào thì bỏ người đó). Đơn vị chỉ có một điểm không ghép cặp được nên bị bỏ,
    vì thế alpha dùng được mọi điểm có sẵn chứ không chỉ những đơn vị ai cũng chấm. None khi không có
    biến thiên nào (mọi điểm bằng nhau), lúc đó không có bất đồng kỳ vọng để so."""
    levels = list(levels)
    pairable = [u for u in units if len(u) >= 2]
    coincidence: dict[tuple[int, int], float] = {}
    for values in pairable:
        m = len(values)
        for i, c in enumerate(values):
            for j, k in enumerate(values):
                if i != j:
                    coincidence[(c, k)] = coincidence.get((c, k), 0.0) + 1 / (m - 1)
    totals: dict[int, float] = {}
    for (c, _), weight in coincidence.items():
        totals[c] = totals.get(c, 0.0) + weight
    n = sum(totals.values())
    if n <= 1:
        return None
    delta = _squared_difference(metric, levels, totals)
    observed = sum(w * delta(c, k) for (c, k), w in coincidence.items())
    expected = sum(totals[c] * totals[k] * delta(c, k) for c in totals for k in totals)
    if expected == 0:
        return None
    return 1 - (n - 1) * observed / expected


def fleiss_kappa(counts: list[list[int]]) -> float | None:
    """Kappa của Fleiss (1971), thang danh nghĩa. Mỗi dòng là một đối tượng, mỗi cột một hạng mục,
    ô là số người xếp đối tượng đó vào hạng mục đó; mọi dòng phải cùng số người chấm."""
    if not counts:
        return None
    raters = sum(counts[0])
    if raters < 2 or any(sum(row) != raters for row in counts):
        raise ValueError("Fleiss' kappa needs the same number (at least two) of raters for every item")
    subjects = len(counts)
    agreement_per_item = [(sum(x * x for x in row) - raters) / (raters * (raters - 1)) for row in counts]
    observed = sum(agreement_per_item) / subjects
    shares = [sum(row[j] for row in counts) / (subjects * raters) for j in range(len(counts[0]))]
    expected = sum(p * p for p in shares)
    return None if expected == 1 else (observed - expected) / (1 - expected)


def reliability(raters: dict[str, dict[str, dict[str, int]]]) -> dict[str, dict]:
    """Độ tin cậy của cả nhóm người chấm cho từng tiêu chí: alpha thứ bậc của Krippendorff trên mọi
    điểm đã cho (chịu được ô thiếu), và kappa của Fleiss trên các gói mà ai cũng chấm, như góp ý yêu
    cầu. Fleiss coi thang 1-5 là danh nghĩa nên thường thấp hơn alpha thứ bậc trên cùng dữ liệu."""
    result = {}
    for c in CRITERIA:
        codes = sorted({code for scores in raters.values() for code, s in scores.items() if c in s})
        units = [[scores[code][c] for scores in raters.values() if c in scores.get(code, {})] for code in codes]
        pairable = [u for u in units if len(u) >= 2]
        if not pairable:
            continue
        complete = [u for u in units if len(u) == len(raters)]
        counts = [[u.count(level) for level in SCALE] for u in complete]
        result[c] = {
            "raters": len(raters), "units": len(pairable), "complete_units": len(complete),
            "krippendorff_alpha_ordinal": krippendorff_alpha(pairable, "ordinal"),
            "krippendorff_alpha_interval": krippendorff_alpha(pairable, "interval"),
            "fleiss_kappa": fleiss_kappa(counts) if complete else None,
        }
    return result


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


def _load_humans(paths: list[str]) -> dict[str, dict[str, dict[str, int]]]:
    """Mỗi tệp một người chấm, đặt tên theo tên tệp không đuôi."""
    humans = {}
    for path in paths:
        name = Path(path).stem
        if name in humans:
            raise SystemExit(f"{path}: hai tệp người chấm cùng tên {name!r}; đổi tên một tệp")
        humans[name] = load_human(path)
    return humans


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
    for group, per_criterion in report.get("reliability", {}).items():
        print(f"\n== Độ tin cậy nhóm {group}")
        print(f"{'tiêu chí':<22}{'người':>6}{'gói':>5}{'đủ':>5}{'α thứ bậc':>11}{'α khoảng':>10}{'Fleiss κ':>10}")
        for c, r in per_criterion.items():
            print(f"{c:<22}{r['raters']:>6}{r['units']:>5}{r['complete_units']:>5}"
                  f"{fmt(r['krippendorff_alpha_ordinal']):>11}{fmt(r['krippendorff_alpha_interval']):>10}"
                  f"{fmt(r['fleiss_kappa']):>10}")
    if "claims" in report:
        print("\nR9 Content claims (pooled counts; incomplete references excluded):")
        print(json.dumps(report["claims"], ensure_ascii=False, indent=2))


def analyze_claims(paths: list[str]) -> dict:
    grouped = {}
    seen = set()
    judge_models = {}
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            judge, model = raw["judge"], raw["judge_model"]
            if judge in judge_models and judge_models[judge] != model:
                raise SystemExit(f"{path}: conflicting R9 judge_model for {judge}: {judge_models[judge]} != {model}")
            judge_models[judge] = model
            key = (raw["judge"], raw["anon_id"])
            if key in seen:
                raise SystemExit(f"{path}: duplicate R9 assessment {key}")
            seen.add(key)
            evaluation = ClaimEvaluation.model_validate(raw["claim_evaluation"])
            grouped.setdefault(raw["judge"], {}).setdefault(raw["group"], []).append(evaluation)
    result = {}
    for judge, groups in grouped.items():
        result[judge] = {}
        for group, rows in groups.items():
            eligible = [r for r in rows if r.unsupported_rate is not None]
            claims = sum(r.total_claims for r in eligible)
            unsupported = sum(r.unsupported_claims for r in eligible)
            citations = sum(r.citation_count or 0 for r in rows)
            correct = sum(r.correct_citations or 0 for r in rows)
            result[judge][group] = {
                "packets": len(rows), "eligible_packets": len(eligible),
                "excluded_packets": len(rows) - len(eligible),
                "total_claims": claims, "unsupported_claims": unsupported,
                "unsupported_rate": unsupported / claims if claims else None,
                "citation_packets": sum(r.citations is not None for r in rows),
                "citation_count": citations, "correct_citations": correct,
                "citation_accuracy": correct / citations if citations else None,
            }
    return result


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("judges", nargs="+", help="các tệp .jsonl của scripts/run_judge.py")
    parser.add_argument("--human", action="append", help="CSV điểm của một người chấm; lặp lại cho mỗi người")
    parser.add_argument("--claims", nargs="+", help="R9 JSONL from run_claim_judge.py")
    parser.add_argument("--out", type=Path, help="ghi thêm báo cáo dạng JSON vào tệp này")
    args = parser.parse_args(argv)

    loaded = load_judgements(args.judges)
    report = {"judges": {}, "agreement": {}}
    if args.claims:
        report["claims"] = analyze_claims(args.claims)
    for name, rows in loaded.items():
        report["judges"][name] = {
            "means": group_means(rows), "wilcoxon": compare_groups(rows), "mcnemar": mcnemar_hallucination(rows),
        }
    names = sorted(loaded)
    for first, second in itertools.combinations(names, 2):
        report["agreement"][f"{first}~{second}"] = agreement(
            scores_by_code(loaded[first]), scores_by_code(loaded[second])
        )
    humans = _load_humans(args.human or [])
    judged_codes = {r.anon_id for rows in loaded.values() for r in rows}
    for rater, human in humans.items():
        if not human.keys() & judged_codes:
            sys.exit(
                f"Người chấm {rater} không có mã ẩn danh nào trùng với tệp của mô hình chấm. Mã M01, M02, ... "
                "của nghiên cứu trong ứng dụng (export_human_ratings.py) và mã M-xxxxxxxx của run_judge.py "
                "là hai hệ khác nhau: dùng judge.jsonl xuất cùng các tệp rater-N.csv, không trộn hai hệ."
            )
    for name in names:
        for rater, human in humans.items():
            report["agreement"][f"{name}~{rater}"] = agreement(scores_by_code(loaded[name]), human)
    for first, second in itertools.combinations(sorted(humans), 2):
        report["agreement"][f"{first}~{second}"] = agreement(humans[first], humans[second])
    if len(humans) >= 2:
        report["reliability"] = {"humans": reliability(humans)}
        for name in names:
            report["reliability"][f"humans+{name}"] = reliability({**humans, name: scores_by_code(loaded[name])})

    _print_report(report)
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
