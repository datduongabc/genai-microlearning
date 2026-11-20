"""The admin sees what the raters gave, in the app: progress, scores and findings per packet, and the
same files the CLI export writes. Ratings go through the raters' own API, as in the export tests."""

import csv
import io

from scripts import analyze_study
from tests.integration.test_blind_ratings import blind as blind_study
from tests.integration.test_blind_ratings import client, evaluation
from tests.integration.test_studies import world as study_world

# Fixtures used by name below, as in test_blind_ratings.
world = study_world
blind = blind_study


def _rate(blind, headers, count: int, score: int) -> list[str]:
    path = f"/studies/mine/{blind['sid']}/items"
    items = client.get(path, headers=headers).json()["items"][:count]
    for item in items:
        body = evaluation(item["anon_code"])
        body["accuracy"]["score"] = score
        assert client.put(f"{path}/{item['id']}/rating", headers=headers, json=body).status_code == 200
    return [item["anon_code"] for item in items]


def test_results_show_progress_scores_findings_and_groups(blind):
    teacher_codes = _rate(blind, blind["teacher_headers"], 3, 2)
    _rate(blind, blind["second_headers"], 1, 5)

    response = client.get(f"/studies/{blind['sid']}/results", headers=blind["headers"])

    assert response.status_code == 200, response.text
    results = response.json()
    assert results["total"] == len(blind["items"])
    # Numbered by email among those who rated, as the CLI export numbers its files.
    numbered = sorted(blind["emails"])
    rated = {blind["emails"][0]: 3, blind["emails"][1]: 1}
    assert {r["email"]: (r["number"], r["rated"]) for r in results["raters"]} == {
        email: (numbered.index(email) + 1, rated[email]) for email in blind["emails"]}
    groups = {item["group"] for item in results["items"]}
    assert groups == {"rag_llm", "llm_no_rag", "human"}
    rated = {item["anon_code"]: item for item in results["items"] if item["ratings"]}
    assert set(teacher_codes) <= set(rated)
    # The admin is not blind to who rated: each rating carries the rater's name.
    teacher_label = {r["email"]: r["name"] for r in results["raters"]}[blind["emails"][0]]
    assert not teacher_label.startswith("Người chấm")
    rating = next(r for r in rated[teacher_codes[0]]["ratings"] if r["rater"] == teacher_label)
    assert rating["kind"] == "human"
    assert rating["scores"]["accuracy"]["score"] == 2
    assert rating["findings"] == [{"claim": "Unsupported", "field": "content", "reason": "No source"}]
    assert rating["notes"] == "Good"


def test_an_invited_rater_who_has_not_rated_shows_with_no_number(blind):
    results = client.get(f"/studies/{blind['sid']}/results", headers=blind["headers"]).json()
    assert [(r["number"], r["rated"], r["invited"]) for r in results["raters"]] == [(None, 0, True)] * 2
    assert all(item["ratings"] == [] for item in results["items"])


def test_rater_csv_is_the_file_the_analysis_reads(blind, tmp_path):
    codes = _rate(blind, blind["second_headers"], 2, 3)
    number = next(r["number"] for r in client.get(
        f"/studies/{blind['sid']}/results", headers=blind["headers"]).json()["raters"] if r["rated"])

    response = client.get(f"/studies/{blind['sid']}/results/raters/{number}.csv", headers=blind["headers"])

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert f'rater-{number}.csv' in response.headers["content-disposition"]
    path = tmp_path / "rater.csv"
    path.write_text(response.text, encoding="utf-8")
    assert {code: s["accuracy"] for code, s in analyze_study.load_human(str(path)).items()} == {c: 3 for c in codes}
    row = next(csv.DictReader(io.StringIO(response.text)))
    assert row["hallucination_notes"] == "content: Unsupported (No source)"


def test_a_rater_number_nobody_has_is_not_found(blind):
    assert client.get(f"/studies/{blind['sid']}/results/raters/1.csv", headers=blind["headers"]).status_code == 404


def test_judge_file_is_empty_until_the_judge_has_rated(blind):
    response = client.get(f"/studies/{blind['sid']}/results/judge.jsonl", headers=blind["headers"])
    assert response.status_code == 404


def test_results_are_for_the_admin_only(blind):
    sid = blind["sid"]
    for path in (f"/studies/{sid}/results", f"/studies/{sid}/results/raters/1.csv", f"/studies/{sid}/results/judge.jsonl"):
        assert client.get(path, headers=blind["teacher_headers"]).status_code == 404
        assert client.get(path).status_code == 401
