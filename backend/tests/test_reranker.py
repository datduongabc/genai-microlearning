"""Reranker trả về mọi chunk đã chấm, điểm cao trước: Adaptive-k cần cả phân bố điểm để tự tìm
chỗ cắt, nên cắt top_k ở đây là mất dữ liệu nó cần. Model chạy ở service reranker, backend chỉ
gọi HTTP, nên ảnh backend không còn torch."""

import tomllib
import types
from pathlib import Path

import httpx
import pytest

from app.retrieval import reranker


def _chunk(text):
    return types.SimpleNamespace(content=text)


class _Service:
    """Trả lời như Text Embeddings Inference: một mục cho mỗi văn bản, xếp theo điểm, kèm chỉ số
    của văn bản trong yêu cầu."""

    def __init__(self, score):
        self.score = score
        self.requests = []

    def __call__(self, url, json, timeout):
        self.requests.append((url, json))
        items = [{"index": i, "score": self.score(text)} for i, text in enumerate(json["texts"])]
        items.sort(key=lambda item: item["score"], reverse=True)
        return httpx.Response(200, json=items, request=httpx.Request("POST", url))


def test_every_chunk_is_scored_and_the_best_comes_first(monkeypatch):
    scores = {"low": 0.1, "high": 0.9, "mid": 0.5}
    monkeypatch.setattr(reranker.httpx, "post", _Service(scores.__getitem__))

    ranked = reranker.rerank_scored("q", [_chunk("low"), _chunk("high"), _chunk("mid")])

    assert [(c.content, s) for c, s in ranked] == [("high", 0.9), ("mid", 0.5), ("low", 0.1)]


def test_the_service_gets_the_query_and_every_chunk_and_scores_on_the_sigmoid_scale(monkeypatch):
    service = _Service(len)
    monkeypatch.setattr(reranker.httpx, "post", service)
    monkeypatch.setattr(reranker.settings, "reranker_url", "http://reranker:80")

    reranker.rerank_scored("what is a heap", [_chunk("a"), _chunk("bb")])

    ((url, body),) = service.requests
    assert url == "http://reranker:80/rerank"
    assert body["query"] == "what is a heap"
    assert body["texts"] == ["a", "bb"]
    # Adaptive-k was tuned on sentence-transformers' scores, which went through a sigmoid.
    assert body["raw_scores"] is False
    assert body["truncate"] is True


def test_rerank_keeps_the_top_k_of_that_order(monkeypatch):
    monkeypatch.setattr(reranker.httpx, "post", _Service(len))

    assert [c.content for c in reranker.rerank("q", [_chunk("a"), _chunk("ccc"), _chunk("bb")], top_k=2)] == ["ccc", "bb"]


def test_nothing_to_rank_does_not_call_the_service(monkeypatch):
    def post(*args, **kwargs):
        raise AssertionError("an empty list must not reach the reranker service")

    monkeypatch.setattr(reranker.httpx, "post", post)

    assert reranker.rerank_scored("q", []) == []


def test_a_failing_service_is_an_error_not_an_unranked_list(monkeypatch):
    def post(url, json, timeout):
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setattr(reranker.httpx, "post", post)

    with pytest.raises(httpx.HTTPStatusError):
        reranker.rerank_scored("q", [_chunk("a")])


def test_the_backend_does_not_depend_on_torch():
    """torch and sentence-transformers made the backend image 5 GB; the model now runs in the
    reranker service of devops/docker-compose.yml."""
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    names = {dep.split(">")[0].split("=")[0].split("<")[0].split("[")[0].strip().lower()
             for dep in pyproject["project"]["dependencies"]}

    assert not names & {"torch", "sentence-transformers", "transformers"}
