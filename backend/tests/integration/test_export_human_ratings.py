"""Điểm chấm mù nằm trong bảng ratings; analyze_study.py đọc mỗi người chấm một CSV. Script xuất nối
hai đầu đó, nên ở đây chấm qua đúng API mà người chấm dùng rồi đọc lại CSV bằng chính bộ đọc của
analyze_study."""

import csv
import uuid

from scripts import analyze_study, export_human_ratings
from tests.integration.test_blind_ratings import blind as blind_study
from tests.integration.test_blind_ratings import client, evaluation
from tests.integration.test_studies import world as study_world

# Fixtures used by name below, as in test_blind_ratings.
world = study_world
blind = blind_study


def _rate(blind, headers, scores_by_position: list[int]) -> dict[str, int]:
    path = f"/studies/mine/{blind['sid']}/items"
    items = client.get(path, headers=headers).json()["items"]
    given = {}
    for item, score in zip(items, scores_by_position, strict=False):
        body = evaluation(item["anon_code"])
        body["accuracy"]["score"] = score
        assert client.put(f"{path}/{item['id']}/rating", headers=headers, json=body).status_code == 200
        given[item["anon_code"]] = score
    return given


def test_each_human_rater_becomes_one_csv_that_the_analysis_reads(blind, tmp_path):
    first = _rate(blind, blind["teacher_headers"], [1, 2, 3])
    second = _rate(blind, blind["second_headers"], [5, 4])

    written = export_human_ratings.export(uuid.UUID(blind["sid"]), tmp_path)

    assert sorted(p.name for p in written) == ["rater-1.csv", "rater-2.csv"]
    loaded = [analyze_study.load_human(str(p)) for p in sorted(written)]
    by_rater = sorted(({code: s["accuracy"] for code, s in r.items()} for r in loaded), key=len)
    assert by_rater == sorted([first, second], key=len)
    with open(min(written), encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["hallucination_notes"] == "content: Unsupported (No source)"


def test_a_study_nobody_rated_writes_nothing(blind, tmp_path):
    assert export_human_ratings.export(uuid.UUID(blind["sid"]), tmp_path) == []
