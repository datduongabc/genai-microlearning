"""Planner chỉ đọc phần đầu tài liệu: cả cuốn giáo trình đi vào một prompt thì một lượt sinh tốn
cả trăm nghìn token, và vượt ngữ cảnh của model nhỏ."""

from app.generation import planner
from app.generation.planner import Concept, PlannerOutput, run_planner


def test_the_document_is_cut_to_the_planner_budget_before_it_is_sent(monkeypatch):
    asked = []

    def fake(prompt, response_model, **kw):
        asked.append(prompt)
        return PlannerOutput(concepts=[Concept(name="Heap", priority=1, summary="Cây nhị phân hoàn chỉnh")])

    monkeypatch.setattr(planner, "complete_structured", fake)
    document = "BAT-DAU " + "x" * (planner.PLANNER_MAX_INPUT_CHARS * 3) + " KET-THUC"

    out = run_planner(document)

    assert out.concepts[0].name == "Heap"
    assert "BAT-DAU" in asked[0] and "KET-THUC" not in asked[0]
    assert len(asked[0]) < planner.PLANNER_MAX_INPUT_CHARS + 2000
