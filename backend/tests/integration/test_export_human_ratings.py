"""Điểm chấm mù nằm trong bảng ratings; analyze_study.py đọc mỗi người chấm một CSV. Script xuất nối
hai đầu đó, nên ở đây chấm qua đúng API mà người chấm dùng rồi đọc lại CSV bằng chính bộ đọc của
analyze_study."""

import csv
import json
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.generation.evaluation import ModuleEvaluation
from app.models import StudyItem
from app.study.judge import upsert_rating
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


def test_exporting_into_a_directory_holding_rater_files_is_refused_and_keeps_them(blind, tmp_path):
    _rate(blind, blind["teacher_headers"], [1, 2, 3])
    _rate(blind, blind["second_headers"], [5, 4])
    stale = tmp_path / "rater-3.csv"
    stale.write_text("cũ", encoding="utf-8")
    first = tmp_path / "rater-1.csv"
    first.write_text("cũ", encoding="utf-8")

    with pytest.raises(SystemExit) as refused:
        export_human_ratings.export(uuid.UUID(blind["sid"]), tmp_path)

    assert "rater-1.csv" in str(refused.value.code) and "rater-3.csv" in str(refused.value.code)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rater-1.csv", "rater-3.csv"]
    assert stale.read_text(encoding="utf-8") == "cũ" and first.read_text(encoding="utf-8") == "cũ"


def _judge_every_item(blind) -> dict[str, dict]:
    """Lượt chấm của mô hình chấm trong ứng dụng, lưu như bước chấm của job; accuracy khác nhau theo gói."""
    with SessionLocal() as db:
        items = db.scalars(select(StudyItem).where(StudyItem.study_id == uuid.UUID(blind["sid"]))).all()
        for n, item in enumerate(sorted(items, key=lambda i: i.anon_code)):
            body = evaluation(item.anon_code)
            body["accuracy"]["score"] = n % 5 + 1
            upsert_rating(db, item, "deepseek-flash", "groq/openai/gpt-oss-120b", ModuleEvaluation.model_validate(body))
        db.commit()
        return {item.anon_code: {"group": item.group} for item in items}


def test_in_app_judge_ratings_are_exported_with_the_raters_codes_and_analysed_together(blind, tmp_path):
    first = _rate(blind, blind["teacher_headers"], [1, 2, 3, 4, 5])
    _rate(blind, blind["second_headers"], [5, 4, 3])
    items = _judge_every_item(blind)

    written = export_human_ratings.export(uuid.UUID(blind["sid"]), tmp_path)

    assert sorted(p.name for p in written) == ["judge.jsonl", "rater-1.csv", "rater-2.csv"]
    lines = [json.loads(line) for line in (tmp_path / "judge.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {line["anon_id"] for line in lines} == set(items)
    assert {line["judge"] for line in lines} == {"deepseek-flash"}
    assert {line["judge_model"] for line in lines} == {"groq/openai/gpt-oss-120b"}
    assert all(line["group"] == items[line["anon_id"]]["group"] for line in lines)
    by_group = {}
    for line in lines:
        by_group.setdefault(line["group"], []).append(line)
    (rag,), (no_rag,) = by_group["rag_llm"], by_group["llm_no_rag"]
    assert rag["pair_id"] == no_rag["pair_id"]
    assert all(line["pair_id"] == line["anon_id"] for line in by_group["human"])
    rater_codes = set().union(*(analyze_study.load_human(str(tmp_path / f"rater-{n}.csv")) for n in (1, 2)))
    assert rater_codes <= set(items)

    report_path = tmp_path / "report.json"
    analyze_study.main([str(tmp_path / "judge.jsonl"), "--human", str(tmp_path / "rater-1.csv"),
                        "--human", str(tmp_path / "rater-2.csv"), "--out", str(report_path)])

    report = json.loads(report_path.read_text(encoding="utf-8"))
    rater = "rater-1" if len(analyze_study.load_human(str(tmp_path / "rater-1.csv"))) == len(first) else "rater-2"
    assert report["agreement"][f"deepseek-flash~{rater}"]["accuracy"]["n"] == len(first)
    assert report["judges"]["deepseek-flash"]["wilcoxon"]["accuracy"]["n"] == 1


def test_a_directory_holding_an_earlier_judge_export_is_refused(blind, tmp_path):
    _judge_every_item(blind)
    earlier = tmp_path / "judge.jsonl"
    earlier.write_text("cũ", encoding="utf-8")

    with pytest.raises(SystemExit) as refused:
        export_human_ratings.export(uuid.UUID(blind["sid"]), tmp_path)

    assert "judge.jsonl" in str(refused.value.code)
    assert earlier.read_text(encoding="utf-8") == "cũ"
