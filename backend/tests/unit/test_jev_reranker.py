"""Jev as the reranker: its order, and that its failure does not fail a generation."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app import timing
from app.config import settings
from app.retrieval import reranker


@pytest.fixture
def jev(monkeypatch):
    monkeypatch.setattr(settings, "reranker_provider", "jev")
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


def test_jina_stays_the_default(monkeypatch):
    assert type(settings).model_fields["reranker_provider"].default == "jina"
