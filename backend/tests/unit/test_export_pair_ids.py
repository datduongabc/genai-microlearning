"""Ghép cặp rag_llm với llm_no_rag khi xuất judge.jsonl: gói nào không ghép được thì phải báo ra, vì
phép kiểm ghép cặp của analyze_study bỏ nó đi mà không nói gì."""

from app.models import StudyItem
from scripts import export_human_ratings


def _item(group, code, objective, subs=("A",)):
    return StudyItem(group=group, anon_code=code, packet={"learning_objective": objective, "sub_objectives": list(subs)})


def test_unpaired_and_ambiguous_items_keep_their_own_code_and_are_named_in_a_warning(capsys):
    items = [
        _item("rag_llm", "M01", "Paired"), _item("llm_no_rag", "M02", "Paired"),
        _item("rag_llm", "M03", "Same"), _item("rag_llm", "M04", "Same"),
        _item("llm_no_rag", "M05", "Same"), _item("llm_no_rag", "M06", "Same"),
        _item("rag_llm", "M07", "Alone"), _item("human", "M08", "Paired"),
    ]

    pair_ids = export_human_ratings._pair_ids(items)

    assert pair_ids == {"M01": "M01", "M02": "M01", "M03": "M03", "M04": "M04", "M05": "M05", "M06": "M06",
                        "M07": "M07", "M08": "M08"}
    warning = capsys.readouterr().out
    assert "M03, M04, M05, M06, M07" in warning
    assert "M01" not in warning and "M02" not in warning and "M08" not in warning


def test_a_fully_paired_study_prints_no_warning(capsys):
    export_human_ratings._pair_ids([_item("rag_llm", "M01", "X"), _item("llm_no_rag", "M02", "X"),
                                    _item("human", "M03", "X")])

    assert capsys.readouterr().out == ""
