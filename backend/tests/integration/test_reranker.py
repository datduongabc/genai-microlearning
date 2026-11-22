"""What the reranker keeps whatever answers it: every chunk comes back, so Adaptive-k sees the whole
score distribution, and the backend carries no model."""

import tomllib
import types
from pathlib import Path

from app import timing
from app.retrieval import reranker


def _chunk(text):
    return types.SimpleNamespace(content=text)


def test_nothing_to_rank_calls_nothing(monkeypatch):
    def ask(*args, **kwargs):
        raise AssertionError("an empty list must not reach Jev")

    monkeypatch.setattr(reranker, "_ask_jev", ask)

    assert reranker.rerank_scored("q", []) == []


def test_without_a_key_nothing_is_sent_and_the_search_order_stands(monkeypatch):
    def ask(*args, **kwargs):
        raise AssertionError("no key: nothing may be sent to TypeSafe")

    monkeypatch.setattr(reranker, "_ask_jev", ask)
    chunks = [_chunk("a"), _chunk("b"), _chunk("c")]

    with timing.capture_timings() as spans:
        ranked = reranker.rerank_scored("q", chunks)

    assert [c.content for c, _ in ranked] == ["a", "b", "c"]
    assert [(s["kind"], s["candidates"], s.get("fallback")) for s in spans] == [("rerank", 3, True)]


def test_rerank_keeps_the_top_k(monkeypatch):
    monkeypatch.setattr(reranker.settings, "typesafe_api_key", "test-key")
    monkeypatch.setattr(reranker, "_ask_jev", lambda query, text, attempts: (len(text) / 10, 0))

    assert [c.content for c in reranker.rerank("q", [_chunk("a"), _chunk("ccc"), _chunk("bb")], top_k=2)] == ["ccc", "bb"]


def test_the_backend_does_not_depend_on_torch():
    """torch and sentence-transformers made the backend image 5 GB; reranking and embeddings are
    provider APIs."""
    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    names = {dep.split(">")[0].split("=")[0].split("<")[0].split("[")[0].strip().lower()
             for dep in pyproject["project"]["dependencies"]}

    assert not names & {"torch", "sentence-transformers", "transformers"}
