"""Unit tests for study item blinding, shuffling, pairing, import, deletion, routes, and AI-original packets."""

import random
import uuid
from collections import Counter
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api import modules
from app.api.deletion import check_study_references, commit_deletion
from app.api.studies import LoadItems
from app.generation.study_packets import module_packet
from app.main import app
from app.models import ComponentType, StudyItem
from app.study.items import assign_codes, shuffle_for_rater
from scripts import export_human_ratings
from scripts.import_study_packets import packets

PACKETS_FOLDER = Path(__file__).parents[3] / "docs/measurements/module-study/packets"


def _study_item(group, *, code=None, objective=None, subs=("A",), anon_id=None, topic=None, extra_packet=None):
    packet = {}
    if objective is not None:
        packet["learning_objective"] = objective
        packet["sub_objectives"] = list(subs)
    if anon_id is not None:
        packet["anon_id"] = anon_id
    if topic is not None:
        packet["topic"] = topic
    if extra_packet:
        packet.update(extra_packet)
    kwargs = {"group": group, "packet": packet}
    if code is not None:
        kwargs["anon_code"] = code
    return StudyItem(**kwargs)


# --- Shuffle & routes -----------------------------------------------------------------------------


def test_shuffle_is_stable_per_rater_without_changing_input():
    study = uuid.UUID(int=1)
    items = list(range(12))
    first = shuffle_for_rater(items, study, uuid.UUID(int=2))
    assert first == shuffle_for_rater(items, study, uuid.UUID(int=2))
    assert first != shuffle_for_rater(items, study, uuid.UUID(int=3))
    assert sorted(first) == items == list(range(12))
    assert shuffle_for_rater([], study, uuid.UUID(int=2)) == []


def test_study_routes_require_authentication_before_database_access():
    client = TestClient(app)
    sid = uuid.uuid4()
    for method, path, body in (
        ("GET", "/studies", None),
        ("GET", f"/studies/{sid}", None),
        ("POST", "/studies", {"name": "S"}),
        ("POST", f"/studies/{sid}/items", {"human_packets": []}),
    ):
        assert client.request(method, path, json=body).status_code == 401


# --- Import study packets -------------------------------------------------------------------------


def test_the_measured_packets_load_as_ten_complete_pairs():
    body = LoadItems(imported_packets=packets(PACKETS_FOLDER))

    assert len(body.imported_packets) == 20
    assert Counter(p.pair_id for p in body.imported_packets).most_common()[-1][1] == 2
    by_pair: dict = {}
    for p in body.imported_packets:
        by_pair.setdefault(p.pair_id, set()).add(p.packet.learning_objective)
    assert all(len(objectives) == 1 for objectives in by_pair.values())


# --- Study items & blinding -----------------------------------------------------------------------


def test_codes_shuffle_all_groups_and_replace_supplied_anonymous_ids(monkeypatch):
    monkeypatch.setattr(random.SystemRandom, "shuffle", lambda self, items: items.reverse())
    items = [_study_item(group, anon_id="revealing-id", topic="Topic") for group in ("rag_llm", "llm_no_rag", "human")]
    assign_codes(items)
    assert [item.anon_code for item in items] == ["M03", "M02", "M01"]
    assert [item.packet["anon_id"] for item in items] == ["M03", "M02", "M01"]
    assert all(item.packet["topic"] == "Topic" for item in items)


def test_loading_more_items_reblinds_existing_codes(monkeypatch):
    monkeypatch.setattr(random.SystemRandom, "shuffle", lambda self, items: items.reverse())
    old = _study_item("human", code="M01", anon_id="M01")
    new = _study_item("rag_llm", anon_id="")
    assign_codes([old, new])
    assert old.anon_code == "M02"
    assert new.anon_code == "M01"


def test_codes_remain_unique_beyond_two_digits():
    items = [_study_item("human") for _ in range(101)]
    assign_codes(items)
    assert {item.anon_code for item in items} == {f"M{i:02d}" for i in range(1, 102)}


# --- Export pair IDs ------------------------------------------------------------------------------


def test_unpaired_and_ambiguous_items_keep_their_own_code_and_are_named_in_a_warning(capsys):
    items = [
        _study_item("rag_llm", code="M01", objective="Paired"),
        _study_item("llm_no_rag", code="M02", objective="Paired"),
        _study_item("rag_llm", code="M03", objective="Same"),
        _study_item("rag_llm", code="M04", objective="Same"),
        _study_item("llm_no_rag", code="M05", objective="Same"),
        _study_item("llm_no_rag", code="M06", objective="Same"),
        _study_item("rag_llm", code="M07", objective="Alone"),
        _study_item("human", code="M08", objective="Paired"),
    ]

    pair_ids = export_human_ratings._pair_ids(items)

    assert pair_ids == {
        "M01": "M01", "M02": "M01", "M03": "M03", "M04": "M04",
        "M05": "M05", "M06": "M06", "M07": "M07", "M08": "M08",
    }
    warning = capsys.readouterr().out
    assert "M03, M04, M05, M06, M07" in warning
    assert "M01" not in warning and "M02" not in warning and "M08" not in warning


def test_a_fully_paired_study_prints_no_warning(capsys):
    export_human_ratings._pair_ids([
        _study_item("rag_llm", code="M01", objective="X"),
        _study_item("llm_no_rag", code="M02", objective="X"),
        _study_item("human", code="M03", objective="X"),
    ])

    assert capsys.readouterr().out == ""


# --- Study deletion -------------------------------------------------------------------------------


def test_referenced_deletion_is_refused():
    db = Mock()
    db.scalar.return_value = True
    with pytest.raises(HTTPException) as error:
        check_study_references(db, [])
    assert error.value.status_code == 409
    db.commit.assert_not_called()


def test_unreferenced_deletion_commits():
    db = Mock()
    db.scalar.return_value = False
    check_study_references(db, [])
    commit_deletion(db)
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


def test_constraint_race_rolls_back_and_returns_conflict():
    db = Mock()
    original = Exception("retained reference")
    original.sqlstate = "23503"
    db.commit.side_effect = IntegrityError("DELETE", {}, original)
    with pytest.raises(HTTPException) as error:
        commit_deletion(db)
    assert error.value.status_code == 409
    db.rollback.assert_called_once()


def test_unrelated_integrity_error_rolls_back_and_propagates():
    db = Mock()
    original = Exception("other constraint")
    original.sqlstate = "23505"
    db.commit.side_effect = IntegrityError("DELETE", {}, original)
    with pytest.raises(IntegrityError):
        commit_deletion(db)
    db.rollback.assert_called_once()


# --- AI original packets --------------------------------------------------------------------------


def _question(stem="AI question", explanation="AI explanation"):
    return NS(
        id="question", stem=stem, choices={"options": ["A", "B"]},
        answer_key="0", explanation=explanation, position=0,
    )


def _lesson():
    components = [NS(type=kind, content=content, questions=questions) for kind, content, questions in [
        (ComponentType.content, {"markdown": "AI text"}, []),
        (ComponentType.slides, {"slides": [{"title": "AI", "bullets": ["AI bullet"]}]}, []),
        (ComponentType.example, {"examples": [{"context": "AI context", "analysis": "AI analysis"}]}, []),
        (ComponentType.quiz, {"questions": []}, [_question()]),
        (ComponentType.practice, {"questions": []}, [_question()]),
        (ComponentType.explanation, {"items": []}, []),
    ]]
    return NS(
        title="Topic", bloom_level="apply", learning_objective="Objective",
        sub_objectives=[], components=components,
    )


def test_first_manual_edit_saves_original_and_later_edit_cannot_replace_it(monkeypatch):
    module = _lesson()
    component = module.components[0]
    component.module_id = "module"
    component.id = "component"
    db = NS(get=lambda *a, **kw: component, commit=lambda: None)
    monkeypatch.setattr(modules, "_owned_module", lambda *a: module)
    for text in ["Teacher first", "Teacher second"]:
        modules.update_component("module", "component", modules.ComponentUpdate(
            content={"markdown": text, "ai_original": {"markdown": "Forged"}}), db, NS())
        assert component.content["ai_original"] == {"markdown": "AI text"}
        assert component.content["markdown"] == text


def test_packet_uses_original_components_and_question_snapshot():
    module = _lesson()
    expected = module_packet(module, ["Reference"])
    for component in module.components[:3]:
        component.content = {"ai_original": component.content}
    for component in module.components[3:5]:
        q = component.questions[0]
        component.content = {"ai_original": {"content": component.content, "questions": [vars(q).copy()]}}
        q.stem = "Teacher question"
        q.explanation = "Teacher explanation"
    module.components[-1].content = {
        "items": [{"component": "quiz", "position": 0, "explanation_text": "Teacher explanation"}]
    }
    assert module_packet(module, ["Reference"]) == expected


def test_question_edit_after_manual_quiz_edit_keeps_original_rows(monkeypatch):
    module = _lesson()
    component = module.components[3]
    component.module_id = "module"
    component.id = "quiz"
    db = NS(get=lambda *a, **kw: component, commit=lambda: None)
    monkeypatch.setattr(modules, "_owned_module", lambda *a: module)
    modules.update_component("module", "quiz", modules.ComponentUpdate(content={"questions": []}), db, NS())
    original = component.content["ai_original"]
    modules._apply_question_update(module, component, component.questions[0], modules.QuestionUpdate(
        stem="Teacher question", choices=["New A", "New B"], answer_key=1,
        explanation="Teacher explanation"))
    assert module_packet(module, []).quiz[0].stem == "AI question"
    assert module_packet(module, []).quiz[0].explanation == "AI explanation"
    assert component.content["ai_original"] == original


def test_original_explanation_component_is_used_for_unedited_questions():
    module = _lesson()
    explanation = module.components[-1]
    explanation.content = {
        "items": [{"component": "quiz", "position": 0, "explanation_text": "Teacher explanation"}],
        "ai_original": {"items": [{"component": "quiz", "position": 0, "explanation_text": "AI override"}]},
    }
    assert module_packet(module, []).quiz[0].explanation == "AI override"


def test_legacy_quiz_edit_then_other_question_edit_keeps_ai_explanation():
    module = _lesson()
    quiz, practice, explanation = module.components[3], module.components[4], module.components[-1]
    quiz_question = quiz.questions[0]
    quiz.content = {**quiz.content, "ai_original": {"content": quiz.content, "questions": [vars(quiz_question).copy()]}}
    quiz_question.explanation = "Teacher quiz explanation"
    explanation.content = {
        "items": [{"component": "quiz", "position": 0, "explanation_text": "Teacher quiz explanation"}]
    }
    modules._apply_question_update(module, practice, practice.questions[0], modules.QuestionUpdate(
        stem="Teacher practice", choices=["New A", "New B"], answer_key=1,
        explanation="Teacher practice explanation"))
    packet = module_packet(module, [])
    assert packet.quiz[0].explanation == "AI explanation"
    assert packet.practice.explanation == "AI explanation"
    assert packet.practice.stem == "AI question"
