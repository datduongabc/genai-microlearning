"""Hợp đồng chung của bốn bước sinh có tự kiểm (Learning Objective, Content, Slides, Example):
sinh xong thì cho model tự kiểm theo từng tiêu chí; trượt tiêu chí nào thì sinh lại một lần và
nói rõ lý do trượt; hết lượt thì trả về bản cuối cùng kèm kết quả kiểm, không ném lỗi, để cả
module vẫn thành hình và kết quả kiểm được ghi lại cho giảng viên xem.

Model được thay bằng một hàm giả ghi lại mọi prompt nó nhận, nên không tốn hạn mức nào."""

import types
import uuid
from dataclasses import dataclass, field

import pytest

from app.generation import content, example, objective, slides
from app.generation.content import ContentOutput, ContentVerification
from app.generation.example import ExampleOutput, ExampleVerification
from app.generation.objective import LearningObjective, LOVerification
from app.generation.planner import Concept
from app.generation.slides import Slide, SlidesOutput, SlidesVerification

LO = LearningObjective(
    bloom_level="apply", verb="Tính", objective_text="Tính tích phân bằng phương pháp đổi biến",
    sub_objectives=["Chọn biến mới phù hợp", "Đổi cận và vi phân"],
)
CHUNKS = [types.SimpleNamespace(id=uuid.uuid4(), content="Đặt u = φ(x) thì du = φ'(x)dx", source_ref="{}")]


@dataclass
class Step:
    name: str
    module: types.ModuleType
    output_model: type
    verification_model: type
    criteria: list[str]
    run: object
    output: object
    asked: list = field(default_factory=list)


STEPS = [
    Step("objective", objective, LearningObjective, LOVerification, ["verb", "atomic", "grounded"],
         lambda: objective.generate_learning_objective(
             Concept(name="Đổi biến", priority=1, summary="Phương pháp đổi biến trong tích phân"), CHUNKS),
         LO),
    Step("content", content, ContentOutput, ContentVerification, ["groundedness", "completeness", "scope"],
         lambda: content.generate_content(LO, CHUNKS),
         ContentOutput(content="## Chọn biến mới\n...", source_locators=["{}"])),
    Step("slides", slides, SlidesOutput, SlidesVerification, ["no_new_info", "conciseness", "cognitive_load"],
         lambda: slides.generate_slides(LO, "## Chọn biến mới\n..."),
         SlidesOutput(slides=[Slide(title="Đổi biến", bullets=["Đặt u = φ(x)"]),
                              Slide(title="Đổi cận", bullets=["Tính cận mới theo u"])])),
    Step("example", example, ExampleOutput, ExampleVerification, ["consistency", "no_overreach", "style"],
         lambda: example.generate_example(LO, "## Chọn biến mới\n...", CHUNKS),
         ExampleOutput(context="Tính ∫2x cos(x²)dx", analysis="Đặt u = x²")),
]


def _verdict(step: Step, failing: dict[str, str]):
    fields = {}
    for criterion in step.criteria:
        fields[f"{criterion}_pass"] = criterion not in failing
        fields[f"{criterion}_reason"] = failing.get(criterion, "đạt")
    return step.verification_model(**fields)


def _install(monkeypatch, step: Step, verdicts: list[dict[str, str]]):
    """Model giả: lượt sinh trả đầu ra cố định, lượt kiểm thứ i trả verdicts[i] (lặp lại cái cuối)."""
    checks = iter(verdicts)
    last: list[dict[str, str]] = [verdicts[-1]]

    def fake(prompt, response_model, system_instruction=None, **kwargs):
        step.asked.append((response_model, prompt))
        if response_model is step.output_model:
            return step.output
        failing = next(checks, last[0])
        return _verdict(step, failing)

    monkeypatch.setattr(step.module, "complete_structured", fake)


def _generations(step: Step) -> list[str]:
    return [prompt for model, prompt in step.asked if model is step.output_model]


@pytest.fixture(params=STEPS, ids=lambda s: s.name)
def step(request):
    request.param.asked.clear()
    return request.param


def test_an_output_that_passes_every_check_is_returned_after_one_generation(step, monkeypatch):
    _install(monkeypatch, step, [{}])

    out, check = step.run()

    assert out == step.output
    assert all(getattr(check, f"{c}_pass") for c in step.criteria)
    assert len(_generations(step)) == 1


def test_a_failed_check_is_retried_with_the_reason_of_every_failed_criterion(step, monkeypatch):
    first, second = step.criteria[0], step.criteria[-1]
    _install(monkeypatch, step, [
        {first: f"LY-DO-{first}: câu này không có trong nguồn", second: f"LY-DO-{second}: thiếu một mục"},
        {},
    ])

    step.run()

    generations = _generations(step)
    assert len(generations) == 2
    assert f"LY-DO-{first}" not in generations[0]
    assert f"LY-DO-{first}" in generations[1] and f"LY-DO-{second}" in generations[1]


def test_a_criterion_that_passed_is_not_reported_as_a_reason_to_fix(step, monkeypatch):
    failing = step.criteria[0]
    _install(monkeypatch, step, [{failing: "LY-DO-TRUOT"}, {}])

    step.run()

    retry = _generations(step)[1]
    assert "LY-DO-TRUOT" in retry
    for passed in step.criteria[1:]:
        assert f"{passed} fail" not in retry.lower()


def test_when_the_retry_also_fails_the_last_output_and_its_verdict_are_returned(step, monkeypatch):
    """Hết lượt không ném lỗi: một phần của module chưa đạt vẫn tốt hơn cả module không được sinh,
    và kết quả kiểm trượt được lưu lại để giảng viên thấy phần nào cần sửa tay."""
    failing = step.criteria[1]
    _install(monkeypatch, step, [{failing: "vẫn trượt"}])

    out, check = step.run()

    assert out == step.output
    assert getattr(check, f"{failing}_pass") is False
    assert len(_generations(step)) == 2
