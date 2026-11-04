"""Phân tích kết quả chấm của nghiên cứu RAG so với không RAG.

Một lỗi ở đây không làm chương trình hỏng, nó chỉ làm sai con số đưa vào luận văn mà bảng vẫn trông
bình thường. Các giá trị mong đợi dưới đây tính tay từ định nghĩa của từng phép kiểm, không đọc từ
cách hàm đang cài đặt:

- Wilcoxon, 4 cặp chênh +1, +2, +3, +4: cả bốn cùng dấu dương, xác suất đúng một trong 2^4 cách
  gán dấu, nên p hai phía = 2/16 = 0,125.
- McNemar chính xác, 4 cặp bất đồng đều cùng một chiều: p hai phía = 2 x 0,5^4 = 0,125.
- Spearman giữa 1,2,3,4,5 và 2,1,4,3,5: tổng d^2 = 4, rho = 1 - 6x4/(5x24) = 0,8.
- Kappa trọng số bậc hai trên thang 1-5 cho cùng hai dãy đó: bất đồng quan sát 4 x (1/16) / 5 = 0,05,
  bất đồng kỳ vọng 100 / (16 x 25) = 0,25, kappa = 1 - 0,05/0,25 = 0,8.
"""

import json

import pytest

from scripts import analyze_study

CRITERIA = analyze_study.CRITERIA


def _line(anon_id, pair_id, group, accuracy=3, other=3, findings=0, judge="j") -> dict:
    scores = {c: {"score": other, "justification": ""} for c in CRITERIA}
    scores["accuracy"] = {"score": accuracy, "justification": ""}
    finding = {"claim": "c", "field": "content", "reason": "r"}
    evaluation = {"anon_id": anon_id, **scores, "hallucination_findings": [finding] * findings, "overall_notes": ""}
    return {"anon_id": anon_id, "pair_id": pair_id, "group": group, "judge": judge, "evaluation": evaluation}


def _write(path, lines) -> str:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return str(path)


@pytest.fixture
def study(tmp_path):
    """Bốn cặp: bản RAG hơn bản không RAG 1, 2, 3, 4 điểm accuracy, các tiêu chí khác bằng nhau, và chỉ
    bản không RAG có hallucination."""
    lines = []
    for n, rag_accuracy in enumerate((2, 3, 4, 5)):
        lines.append(_line(f"R{n}", f"cap-{n}", "rag_llm", accuracy=rag_accuracy))
        lines.append(_line(f"N{n}", f"cap-{n}", "llm_no_rag", accuracy=1, findings=2))
    return analyze_study.load_judgements([_write(tmp_path / "judge.jsonl", lines)])["j"]


def test_the_criteria_are_the_eight_scored_dimensions_of_the_rubric():
    assert CRITERIA == [
        "accuracy", "completeness", "consistency", "hallucination_score",
        "slide_quality", "quiz_quality", "bloom_fidelity", "query_alignment",
    ]


def test_group_means(study):
    means = analyze_study.group_means(study)

    assert means["rag_llm"]["accuracy"] == 3.5
    assert means["llm_no_rag"]["accuracy"] == 1.0
    assert means["rag_llm"]["quiz_quality"] == means["llm_no_rag"]["quiz_quality"] == 3.0


def test_wilcoxon_per_criterion_matches_the_exact_hand_computed_p_value(study):
    result = analyze_study.compare_groups(study)

    assert result["accuracy"]["n"] == 4
    assert result["accuracy"]["mean_diff"] == 2.5
    assert result["accuracy"]["p"] == pytest.approx(0.125)


def test_a_criterion_where_every_pair_ties_has_no_evidence_of_a_difference(study):
    result = analyze_study.compare_groups(study)

    assert result["quiz_quality"]["mean_diff"] == 0
    assert result["quiz_quality"]["p"] == 1.0


def test_mcnemar_on_having_at_least_one_hallucination(study):
    result = analyze_study.mcnemar_hallucination(study)

    assert (result["rag_only"], result["no_rag_only"]) == (0, 4)
    assert result["p"] == pytest.approx(0.125)


def test_a_packet_whose_pair_is_missing_is_left_out_of_the_paired_tests(tmp_path, study):
    alone = analyze_study.load_judgements([_write(tmp_path / "x.jsonl", [_line("L", "le", "rag_llm", accuracy=5)])])["j"]

    assert analyze_study.compare_groups(study + alone)["accuracy"]["n"] == 4


def test_holm_correction():
    assert analyze_study.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_weighted_kappa_and_spearman_between_two_raters():
    first = {f"M{i}": {"accuracy": s} for i, s in enumerate((1, 2, 3, 4, 5))}
    second = {f"M{i}": {"accuracy": s} for i, s in enumerate((2, 1, 4, 3, 5))}

    result = analyze_study.agreement(first, second)["accuracy"]

    assert result["n"] == 5
    assert result["spearman"] == pytest.approx(0.8)
    assert result["kappa"] == pytest.approx(0.8)


def test_identical_raters_agree_perfectly():
    assert analyze_study.quadratic_weighted_kappa([1, 3, 5, 2], [1, 3, 5, 2]) == pytest.approx(1.0)


def test_human_ratings_are_matched_to_the_judge_by_anonymous_code(tmp_path, study):
    header = "anon_id," + ",".join(CRITERIA)
    rows = [f"{r.anon_id}," + ",".join(str(r.scores[c]) for c in CRITERIA) for r in reversed(study)]
    human = tmp_path / "human.csv"
    human.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")

    result = analyze_study.agreement(analyze_study.scores_by_code(study), analyze_study.load_human(str(human)))

    assert result["accuracy"]["n"] == 8
    assert result["accuracy"]["kappa"] == pytest.approx(1.0)


def test_a_human_score_outside_the_scale_is_rejected(tmp_path):
    human = tmp_path / "human.csv"
    human.write_text("anon_id,accuracy\nM1,7\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        analyze_study.load_human(str(human))


def _lines_of(study, judge) -> list[dict]:
    return [_line(r.anon_id, r.pair_id, r.group, r.scores["accuracy"], judge=judge) for r in study]


def test_the_report_covers_both_judges_and_the_human(tmp_path, study):
    first = _write(tmp_path / "a.jsonl", _lines_of(study, "a"))
    second = _write(tmp_path / "b.jsonl", _lines_of(study, "b"))
    human = tmp_path / "human.csv"
    human.write_text("anon_id,accuracy\n" + "".join(f"{r.anon_id},{r.scores['accuracy']}\n" for r in study))
    out = tmp_path / "report.json"

    analyze_study.main([first, second, "--human", str(human), "--out", str(out)])

    report = json.loads(out.read_text(encoding="utf-8"))
    assert set(report["judges"]) == {"a", "b"}
    assert report["judges"]["a"]["wilcoxon"]["accuracy"]["p"] == pytest.approx(0.125)
    assert report["agreement"]["a~b"]["accuracy"]["kappa"] == pytest.approx(1.0)
    assert report["agreement"]["a~human"]["accuracy"]["n"] == 8


def test_one_file_holding_two_judges_is_split_by_judge(tmp_path, study):
    """run_judge ghi mô hình chấm thứ hai vào cùng tệp: phân tích tách thành hai mô hình chấm, không
    gộp điểm của cả hai vào một bảng."""
    mixed = _write(tmp_path / "r.jsonl", _lines_of(study, "claude-pro") + _lines_of(study, "groq-pro"))
    out = tmp_path / "report.json"

    analyze_study.main([mixed, "--out", str(out)])

    report = json.loads(out.read_text(encoding="utf-8"))
    assert set(report["judges"]) == {"claude-pro", "groq-pro"}
    for judge in report["judges"].values():
        assert judge["wilcoxon"]["accuracy"]["n"] == 4
        assert judge["wilcoxon"]["accuracy"]["p"] == pytest.approx(0.125)
    assert report["agreement"]["claude-pro~groq-pro"]["accuracy"]["n"] == 8
    assert report["agreement"]["claude-pro~groq-pro"]["accuracy"]["kappa"] == pytest.approx(1.0)


def test_a_packet_judged_twice_by_the_same_judge_is_refused(tmp_path, study):
    first = _write(tmp_path / "a.jsonl", _lines_of(study, "claude-pro"))
    again = _write(tmp_path / "b.jsonl", _lines_of(study, "claude-pro")[:1])

    with pytest.raises(SystemExit) as refused:
        analyze_study.load_judgements([first, again])

    assert "claude-pro" in str(refused.value.code)


# --- Độ tin cậy giữa nhiều người chấm ----------------------------------------------------------

# Ví dụ chuẩn của Krippendorff (2011), "Computing Krippendorff's Alpha-Reliability", mục C: bốn người
# quan sát, mười hai đơn vị, có ô thiếu. Bài báo công bố alpha danh nghĩa 0,743, thứ bậc 0,815 và
# khoảng 0,849 cho đúng dữ liệu này, nên đây là giá trị mong đợi độc lập với cách cài đặt.
_KRIPPENDORFF_2011 = {
    "A": [1, 2, 3, 3, 2, 1, 4, 1, 2, None, None, None],
    "B": [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, None, 3],
    "C": [None, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, None],
    "D": [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, None],
}


def _units(table: dict[str, list]) -> list[list[int]]:
    return [[row[u] for row in table.values() if row[u] is not None] for u in range(len(next(iter(table.values()))))]


@pytest.mark.parametrize(("metric", "expected"), [("nominal", 0.743), ("ordinal", 0.815), ("interval", 0.849)])
def test_krippendorff_alpha_matches_the_published_worked_example(metric, expected):
    alpha = analyze_study.krippendorff_alpha(_units(_KRIPPENDORFF_2011), metric=metric, levels=range(1, 6))

    assert alpha == pytest.approx(expected, abs=0.0005)


def test_alpha_is_one_when_every_rater_agrees_and_undefined_without_variation():
    assert analyze_study.krippendorff_alpha([[2, 2], [4, 4, 4], [5, 5]], levels=range(1, 6)) == pytest.approx(1.0)
    # Mọi giá trị bằng nhau: không có bất đồng kỳ vọng nào để so, nên alpha không xác định.
    assert analyze_study.krippendorff_alpha([[3, 3], [3, 3]], levels=range(1, 6)) is None


def test_fleiss_kappa_matches_the_classic_worked_example():
    """Ví dụ của Fleiss trên Wikipedia: 10 đối tượng, 14 người chấm, 5 hạng mục, kappa = 0,210."""
    counts = [
        [0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1],
        [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2], [6, 5, 2, 1, 0], [0, 2, 2, 3, 7],
    ]

    assert analyze_study.fleiss_kappa(counts) == pytest.approx(0.210, abs=0.0005)


def _human_csv(path, scores: dict[str, int]):
    path.write_text("anon_id,accuracy\n" + "".join(f"{code},{s}\n" for code, s in scores.items()), encoding="utf-8")
    return str(path)


def test_reliability_among_human_raters_uses_every_rating_and_fleiss_only_complete_items(tmp_path):
    raters = {
        "an": {"M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5},
        "binh": {"M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5},
        "chi": {"M1": 1, "M2": 2, "M3": 3, "M4": 4},
    }
    loaded = {name: analyze_study.load_human(_human_csv(tmp_path / f"{name}.csv", s)) for name, s in raters.items()}

    result = analyze_study.reliability(loaded)["accuracy"]

    assert result["raters"] == 3
    assert result["units"] == 5
    assert result["complete_units"] == 4
    assert result["krippendorff_alpha_ordinal"] == pytest.approx(1.0)
    assert result["fleiss_kappa"] == pytest.approx(1.0)


def test_report_with_several_human_files_adds_pairwise_and_group_reliability(tmp_path, study):
    judge = _write(tmp_path / "a.jsonl", _lines_of(study, "a"))
    truth = {r.anon_id: r.scores["accuracy"] for r in study}
    first = _human_csv(tmp_path / "an.csv", truth)
    second = _human_csv(tmp_path / "binh.csv", truth)
    out = tmp_path / "report.json"

    analyze_study.main([judge, "--human", first, "--human", second, "--out", str(out)])

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["agreement"]["an~binh"]["accuracy"]["kappa"] == pytest.approx(1.0)
    assert report["agreement"]["a~an"]["accuracy"]["n"] == 8
    assert report["reliability"]["humans"]["accuracy"]["krippendorff_alpha_ordinal"] == pytest.approx(1.0)
    # Mô hình chấm có thay được một người chấm không: alpha của nhóm người khi thêm nó vào.
    assert report["reliability"]["humans+a"]["accuracy"]["krippendorff_alpha_ordinal"] == pytest.approx(1.0)


def test_a_human_file_sharing_no_code_with_any_judge_is_refused(tmp_path, study):
    """CSV mang mã M01 của nghiên cứu trong ứng dụng, tệp chấm mang mã của run_judge: không ghép được gói
    nào, nên dừng thay vì báo độ đồng thuận rỗng."""
    judge = _write(tmp_path / "a.jsonl", _lines_of(study, "a"))
    matching = _human_csv(tmp_path / "an.csv", {r.anon_id: r.scores["accuracy"] for r in study})
    foreign = _human_csv(tmp_path / "rater-1.csv", {"M01": 3, "M02": 4})

    with pytest.raises(SystemExit) as refused:
        analyze_study.main([judge, "--human", matching, "--human", foreign])

    assert "rater-1" in str(refused.value.code)
