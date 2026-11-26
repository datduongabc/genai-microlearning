"""Unit tests for planner budgeting, self-correction loop, provenance, parallel questions, daily limits, and scope."""

import threading
import types
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.modules import GenerateRequest
from app.db import get_db
from app.generation import (
    content,
    example,
    limits,
    objective,
    planner,
    slides,
)
from app.generation import (
    module_generation_pipeline as pipeline,
)
from app.generation.content import ContentOutput, ContentVerification
from app.generation.example import Example, ExampleOutput
from app.generation.module_generation_pipeline import (
    control_payload,
    generation_payload,
    normalize_generation_payload,
)
from app.generation.objective import LearningObjective
from app.generation.planner import Concept, PlannerOutput, run_planner
from app.generation.slides import Slide, SlidesOutput
from app.llm import gateway
from app.llm.gateway import active_model, requested_model, using_model
from app.main import app
from app.models import ComponentType

# --- Planner --------------------------------------------------------------------------------------


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


# --- Self-correction ------------------------------------------------------------------------------


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
    Step("content", content, ContentOutput, ContentVerification, ["groundedness", "completeness", "scope"],
         lambda: content.generate_content(LO, CHUNKS),
         ContentOutput(content="## Chọn biến mới\n...")),
]

UNCHECKED = [
    ("objective", objective, LearningObjective, LO,
     lambda: objective.generate_learning_objective(
         Concept(name="Đổi biến", priority=1, summary="Phương pháp đổi biến trong tích phân"), CHUNKS)),
    ("slides", slides, SlidesOutput,
     SlidesOutput(slides=[Slide(title="Đổi biến", bullets=["Đặt u = φ(x)"]),
                          Slide(title="Đổi cận", bullets=["Tính cận mới theo u"])]),
     lambda: slides.generate_slides(LO, "## Chọn biến mới\n...")),
    ("example", example, ExampleOutput,
     ExampleOutput(examples=[Example(context="Tính ∫2x cos(x²)dx", analysis="Đặt u = x²")] * 2),
     lambda: example.generate_example(LO, "## Chọn biến mới\n...", CHUNKS)),
]


def _verdict(step: Step, failing: dict[str, str]):
    fields = {}
    for criterion in step.criteria:
        fields[f"{criterion}_pass"] = criterion not in failing
        fields[f"{criterion}_reason"] = failing.get(criterion, "đạt")
    return step.verification_model(**fields)


def _install(monkeypatch, step: Step, verdicts: list[dict[str, str]]):
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

    assert out.content == step.output.content
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
    failing = step.criteria[1]
    _install(monkeypatch, step, [{failing: "vẫn trượt"}])

    out, check = step.run()

    assert out.content == step.output.content
    assert getattr(check, f"{failing}_pass") is False
    assert len(_generations(step)) == 2


def test_content_without_self_check_is_written_once_and_never_checked(monkeypatch):
    step = STEPS[0]
    step.asked.clear()
    _install(monkeypatch, step, [{step.criteria[0]: "sẽ không ai hỏi tới"}])

    out, check = content.generate_content(LO, CHUNKS, self_check=False)

    assert out.content == step.output.content and check is None
    assert [model for model, _ in step.asked] == [ContentOutput]


@pytest.mark.parametrize("name, module, output_model, output, run", UNCHECKED, ids=[u[0] for u in UNCHECKED])
def test_objective_slides_and_example_are_one_call_with_no_check(name, module, output_model, output, run, monkeypatch):
    asked = []

    def fake(prompt, response_model, system_instruction=None, **kwargs):
        asked.append(response_model)
        return output

    monkeypatch.setattr(module, "complete_structured", fake)

    assert run() == output
    assert asked == [output_model]


# --- Generation provenance ------------------------------------------------------------------------


def test_default_selection_keeps_the_same_payload_as_legacy_jobs(monkeypatch):
    from app.llm.gateway import settings

    monkeypatch.setattr(settings, "llm_model", "default-model")
    with using_model(None):
        assert active_model() == "default-model"
        assert generation_payload(requested_model(), False) == generation_payload(None, False)
        paired_id = uuid.uuid4()
        assert control_payload(paired_id, requested_model(), False) == control_payload(paired_id, None, False)


def test_explicit_alias_is_preserved_and_does_not_match_other_selections():
    paired_id = uuid.uuid4()
    with using_model("explicit-model"):
        assert active_model() == requested_model() == "explicit-model"
        saved = control_payload(paired_id, requested_model(), False)
        assert saved == control_payload(paired_id, "explicit-model", False)
        assert saved != control_payload(paired_id, None, False)
        assert saved != control_payload(paired_id, "other-model", False)
        with using_model(None):
            assert requested_model() is None
        assert requested_model() == "explicit-model"


def test_missing_conditions_match_explicit_defaults_without_changing_alias():
    legacy = {"model_alias": "test-model", "use_retrieval": False, "paired_module_id": str(uuid.uuid4())}
    expected = {**legacy, "self_check": True, "output_language": "vi", "custom_instructions": ""}
    assert normalize_generation_payload(legacy) == normalize_generation_payload(expected)


@pytest.mark.parametrize("field_name,value", [
    ("self_check", False), ("output_language", "en"), ("custom_instructions", "Explain X"),
    ("use_retrieval", False), ("model_alias", "explicit-model"),
])
def test_explicit_condition_differences_remain_distinct(field_name, value):
    assert normalize_generation_payload({field_name: value}) != normalize_generation_payload({})


# --- Parallel questions ---------------------------------------------------------------------------


def _generate_questions_with(monkeypatch, fake):
    monkeypatch.setattr(pipeline, "generate_quiz_question", fake)
    obj = SimpleNamespace(sub_objectives=["first", "second", "third"])
    return pipeline._generate_questions(None, SimpleNamespace(course_id="course"), obj, "main idea", None)


def test_questions_run_at_the_same_time_and_keep_their_order(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "question_concurrency", 4)
    together = threading.Barrier(4, timeout=5)

    def fake(db, course_id, target, lo, mode, scope, **kwargs):
        together.wait()
        return {"question": None, "target": target, "mode": mode}

    asked = _generate_questions_with(monkeypatch, fake)
    assert [a.target for a in asked] == ["first", "second", "third", "main idea"]
    assert [a.type for a in asked] == [ComponentType.quiz] * 3 + [ComponentType.practice]
    assert [a.state["mode"] for a in asked] == ["quiz", "quiz", "quiz", "practice"]


def test_each_question_runs_on_the_model_chosen_for_the_generation(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "question_concurrency", 4)
    seen = []

    def fake(db, course_id, target, lo, mode, scope, **kwargs):
        seen.append(gateway.active_model())
        return {"question": None}

    with gateway.using_model("claude-fast"):
        _generate_questions_with(monkeypatch, fake)
    assert seen == ["claude-fast"] * 4


# --- Daily limits timezone ------------------------------------------------------------------------


class _Clock(datetime):
    """datetime.now at a fixed instant, in whatever zone it is asked for."""
    instant = datetime(2026, 11, 18, 17, 30, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz)


def test_the_day_turns_at_midnight_in_vietnam_not_in_utc(monkeypatch):
    monkeypatch.setattr(limits, "datetime", _Clock)
    assert limits.today() == date(2026, 11, 19)
    _Clock.instant = datetime(2026, 11, 18, 16, 59, tzinfo=UTC)
    assert limits.today() == date(2026, 11, 18)


# --- Document scope contract ----------------------------------------------------------------------


def test_upload_has_no_document_kind_and_sections_have_no_endpoint():
    schema = app.openapi()
    parameters = schema["paths"]["/documents/uploads"]["post"]["parameters"]
    assert "kind" not in {p["name"] for p in parameters}
    assert set(schema["components"]["schemas"]["UploadRequest"]["properties"]) == {"filename", "size"}
    assert "/documents/{document_id}/sections" not in schema["paths"]


@pytest.fixture
def anonymous_client():
    app.dependency_overrides[get_current_user] = lambda: None
    app.dependency_overrides[get_db] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_generation_rejects_removed_section_scope(anonymous_client):
    response = anonymous_client.post(
        "/modules/generate", params={"document_id": str(uuid.uuid4())},
        json={"section_ids": [str(uuid.uuid4())]},
    )

    assert response.status_code == 422
    assert "section_ids" in response.json()["detail"][0]["msg"]
    assert "gỡ bỏ" in response.json()["detail"][0]["msg"]


def test_generation_request_ignores_other_unknown_fields():
    body = GenerateRequest.model_validate({"model_alias": "claude-fast", "unknown": 1})

    assert body.model_alias == "claude-fast"
