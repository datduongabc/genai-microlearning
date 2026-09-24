"""Reranker trả về mọi chunk đã chấm, điểm cao trước: Adaptive-k cần cả phân bố điểm để tự tìm
chỗ cắt, nên cắt top_k ở đây là mất dữ liệu nó cần."""

import types

from app.retrieval import reranker


def _chunk(text):
    return types.SimpleNamespace(content=text)


def test_every_chunk_is_scored_and_the_best_comes_first(monkeypatch):
    scores = {"low": 0.1, "high": 0.9, "mid": 0.5}
    model = types.SimpleNamespace(predict=lambda pairs: [scores[text] for _, text in pairs])
    monkeypatch.setattr(reranker, "_get_model", lambda: model)

    ranked = reranker.rerank_scored("q", [_chunk("low"), _chunk("high"), _chunk("mid")])

    assert [c.content for c, _ in ranked] == ["high", "mid", "low"]


def test_rerank_keeps_the_top_k_of_that_order(monkeypatch):
    model = types.SimpleNamespace(predict=lambda pairs: [len(text) for _, text in pairs])
    monkeypatch.setattr(reranker, "_get_model", lambda: model)

    assert [c.content for c in reranker.rerank("q", [_chunk("a"), _chunk("ccc"), _chunk("bb")], top_k=2)] == ["ccc", "bb"]


def test_nothing_to_rank_does_not_load_the_model(monkeypatch):
    def load():
        raise AssertionError("the 1 GB model must not be loaded for an empty list")

    monkeypatch.setattr(reranker, "_get_model", load)

    assert reranker.rerank_scored("q", []) == []
