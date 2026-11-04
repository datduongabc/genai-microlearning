"""Span thời gian của một lượt sinh: mỗi giai đoạn một span, mỗi lệnh gọi bên trong gắn vào giai
đoạn đang chạy, và ngoài một lượt đo thì không ghi gì (worker chạy nhiều lượt cùng lúc)."""

import pytest

from app import timing


def test_calls_are_attributed_to_the_stage_that_made_them():
    with timing.capture_timings() as spans:
        with timing.stage("content"), timing.timed("llm", completion_tokens=7):
            pass
        with timing.timed("rerank", candidates=30):
            pass

    assert [(s["kind"], s["stage"]) for s in spans] == [("llm", "content"), ("stage", "content"), ("rerank", None)]
    assert spans[0]["completion_tokens"] == 7
    assert spans[2]["candidates"] == 30
    assert all(s["seconds"] >= 0 for s in spans)


def test_nothing_is_recorded_outside_a_capture():
    with timing.stage("content"), timing.timed("llm"):
        pass

    with timing.capture_timings() as spans:
        pass
    assert spans == []


def test_a_stage_that_raises_is_still_recorded_and_marked_failed():
    with timing.capture_timings() as spans, pytest.raises(RuntimeError), timing.stage("slides"):
        raise RuntimeError("provider down")

    assert spans == [{"kind": "stage", "stage": "slides", "seconds": spans[0]["seconds"], "failed": True}]


def test_summary_totals_seconds_and_tokens_per_stage():
    spans = [
        {"kind": "llm", "stage": "content", "seconds": 2.0, "prompt_tokens": 100, "completion_tokens": 50},
        {"kind": "llm", "stage": "content", "seconds": 1.0, "prompt_tokens": 10, "completion_tokens": 5},
        {"kind": "rerank", "stage": "content", "seconds": 0.5},
        {"kind": "stage", "stage": "content", "seconds": 4.0},
    ]

    summary = timing.summarize(spans)

    assert summary["content"] == {
        "seconds": 4.0, "llm_calls": 2, "llm_seconds": 3.0, "prompt_tokens": 110, "completion_tokens": 55,
        "rerank_seconds": 0.5,
    }


def test_profile_statistics_use_nearest_rank_percentiles():
    from scripts.profile_generation import _thong_ke

    stats = _thong_ke([float(x) for x in range(1, 21)])

    assert stats == {"n": 20, "mean": 10.5, "p50": 11.0, "p95": 19.0, "max": 20.0}
    assert _thong_ke([]) == {"n": 0}
