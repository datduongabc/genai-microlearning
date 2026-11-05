"""The questions of a module do not depend on each other, so they are generated at once."""

import threading
from types import SimpleNamespace

from app.generation import module_generation_pipeline as pipeline
from app.llm import gateway
from app.models import ComponentType


def _generate(monkeypatch, fake):
    monkeypatch.setattr(pipeline, "generate_quiz_question", fake)
    objective = SimpleNamespace(sub_objectives=["first", "second", "third"])
    return pipeline._generate_questions(None, SimpleNamespace(course_id="course"), objective, "main idea", None)


def test_questions_run_at_the_same_time_and_keep_their_order(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "question_concurrency", 4)
    # Passes only if all four calls are in progress together.
    together = threading.Barrier(4, timeout=5)

    def fake(db, course_id, target, lo, mode, scope, **kwargs):
        together.wait()
        return {"question": None, "target": target, "mode": mode}

    asked = _generate(monkeypatch, fake)
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
        _generate(monkeypatch, fake)
    assert seen == ["claude-fast"] * 4
