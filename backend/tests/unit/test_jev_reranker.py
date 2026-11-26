"""Jev as the reranker: its order, and that its failure does not fail a generation."""

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app import timing
from app.config import settings
from app.retrieval import reranker


@pytest.fixture
def jev(monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", "test-key")
    monkeypatch.setattr(reranker.time, "sleep", lambda _: None)

    def use(handler):
        monkeypatch.setattr(reranker, "_jev_client", httpx.Client(transport=httpx.MockTransport(handler)))

    return use


def _chunks(*texts):
    return [SimpleNamespace(content=text) for text in texts]


def _noul(value: float) -> httpx.Response:
    return httpx.Response(200, json={"answers": {"relevant": {"type": "noul", "noul": value}}})


def test_best_passage_comes_first_and_ties_keep_the_search_order(jev):
    scores = {"a": 0.2, "b": 0.9, "c": 0.2}
    jev(lambda request: _noul(scores[json.loads(request.content)["state"]["passage"]]))

    ranked = reranker.rerank_scored("q", _chunks("a", "b", "c"))

    assert [c.content for c, _ in ranked] == ["b", "a", "c"]


def test_the_model_stays_pinned_and_the_query_goes_with_each_passage(jev, monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", "secret")
    seen = []

    def handler(request):
        seen.append(request)
        return _noul(0.5)

    jev(handler)
    reranker.rerank_scored("the query", _chunks("a"))

    body = json.loads(seen[0].content)
    assert body["model"] == "jev-1.14.0"
    assert body["state"] == {"query": "the query", "passage": "a"}
    assert seen[0].headers["authorization"] == "Bearer secret"


def test_rate_limit_is_retried_and_counted(jev):
    answers = iter([httpx.Response(429), httpx.Response(529), _noul(0.7)])
    jev(lambda _: next(answers))

    with timing.capture_timings() as spans:
        ranked = reranker.rerank_scored("q", _chunks("a"))

    assert ranked[0][1] == 0.7
    assert spans[0]["retries"] == 2
    assert "fallback" not in spans[0]


@pytest.mark.parametrize("failure", [lambda _: httpx.Response(401), lambda _: httpx.Response(529), lambda _: (_ for _ in ()).throw(httpx.ConnectError("down"))])
def test_a_failed_call_keeps_the_search_order_and_is_recorded(jev, failure):
    jev(failure)

    with timing.capture_timings() as spans:
        ranked = reranker.rerank_scored("q", _chunks("a", "b", "c"))

    assert [c.content for c, _ in ranked] == ["a", "b", "c"]
    assert spans[0]["fallback"] is True


@pytest.mark.parametrize(("status", "level"), [(402, "ERROR"), (401, "ERROR"), (529, "WARNING")])
def test_a_fallback_is_logged_for_an_alert_and_running_out_of_credit_stands_out(jev, caplog, status, level):
    jev(lambda _: httpx.Response(status))

    reranker.rerank_scored("q", _chunks("a"))

    record = next(r for r in caplog.records if r.getMessage().startswith("jev_fallback"))
    assert record.levelname == level and f"status={status}" in record.getMessage()


def test_a_caller_with_someone_waiting_can_limit_the_tries(jev):
    calls = []

    def busy(_):
        calls.append(1)
        return httpx.Response(429)

    jev(busy)

    reranker.rerank_scored("q", _chunks("a"), attempts=2)

    assert len(calls) == 2


@pytest.mark.parametrize("retry_after", ["120", "Wed, 21 Oct 2026 07:28:00 GMT", "nan", "-3"])
def test_a_long_or_odd_retry_after_waits_a_few_seconds_at_most(jev, monkeypatch, retry_after):
    # A wait of minutes would hold one of the shared rerank threads, while keeping the search
    # order (the fallback) costs only a little ranking quality.
    waits = []
    monkeypatch.setattr(reranker.time, "sleep", waits.append)
    answers = iter([httpx.Response(429, headers={"retry-after": retry_after}), _noul(0.7)])
    jev(lambda _: next(answers))

    ranked = reranker.rerank_scored("q", _chunks("a"))

    assert ranked[0][1] == 0.7
    assert len(waits) == 1 and 0 <= waits[0] <= reranker.MAX_RETRY_AFTER_SECONDS


def test_a_missing_key_is_logged_once_in_the_words_the_alarm_counts(monkeypatch, caplog):
    monkeypatch.setattr(settings, "typesafe_api_key", "")
    monkeypatch.setattr(reranker, "_no_key_reported", False)

    reranker.rerank_scored("q", _chunks("a"))
    reranker.rerank_scored("q", _chunks("b"))

    assert [r.getMessage() for r in caplog.records if r.getMessage().startswith("jev_fallback")] == [
        "jev_fallback status=None, keeping the search order: no TYPESAFE_API_KEY"
    ]


def test_nothing_to_rank_calls_nothing(monkeypatch):
    def ask(*args, **kwargs):
        raise AssertionError("an empty list must not reach Jev")

    monkeypatch.setattr(reranker, "_ask_jev", ask)

    assert reranker.rerank_scored("q", []) == []


def test_without_a_key_nothing_is_sent_and_the_search_order_stands(monkeypatch):
    def ask(*args, **kwargs):
        raise AssertionError("no key: nothing may be sent to TypeSafe")

    monkeypatch.setattr(reranker, "_ask_jev", ask)
    chunks = _chunks("a", "b", "c")

    with timing.capture_timings() as spans:
        ranked = reranker.rerank_scored("q", chunks)

    assert [c.content for c, _ in ranked] == ["a", "b", "c"]
    assert [(s["kind"], s["candidates"], s.get("fallback")) for s in spans] == [("rerank", 3, True)]


def test_rerank_keeps_the_top_k(monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", "test-key")
    monkeypatch.setattr(reranker, "_ask_jev", lambda query, text, attempts: (len(text) / 10, 0))

    assert [c.content for c in reranker.rerank("q", _chunks("a", "ccc", "bb"), top_k=2)] == ["ccc", "bb"]


def test_the_backend_does_not_depend_on_torch():
    """torch and sentence-transformers made the backend image 5 GB; reranking and embeddings are
    provider APIs."""
    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    names = {dep.split(">")[0].split("=")[0].split("<")[0].split("[")[0].strip().lower()
             for dep in pyproject["project"]["dependencies"]}

    assert not names & {"torch", "sentence-transformers", "transformers"}
