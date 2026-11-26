import uuid

import pytest

from app.models import Chunk
from app.retrieval.search import _adaptive_k_largest_gap, _tokenize, reciprocal_rank_fusion


def _make_chunk(content="text"):
    return Chunk(id=uuid.uuid4(), content=content)


def test_tokenize_lowercases_and_splits_words():
    assert _tokenize("Hello, World!") == ["hello", "world"]


def test_reciprocal_rank_fusion_prefers_items_ranked_high_in_both_lists():
    a, b, c = _make_chunk("a"), _make_chunk("b"), _make_chunk("c")
    dense = [a, b, c]
    sparse = [b, a, c]
    fused = reciprocal_rank_fusion([dense, sparse])
    # a and b are both near the top of both rankings -> tied ahead of c
    # (which is always last in both).
    assert fused[-1].id == c.id
    assert {fused[0].id, fused[1].id} == {a.id, b.id}


def test_reciprocal_rank_fusion_empty_rankings_returns_empty():
    assert reciprocal_rank_fusion([[], []]) == []


def test_reciprocal_rank_fusion_deduplicates_chunk_appearing_in_both():
    a = _make_chunk("a")
    b = _make_chunk("b")
    fused = reciprocal_rank_fusion([[a], [a]])
    assert len(fused) == 1
    # k=0 verifies the denominator is (k + rank + 1), not (k + rank - 1) which would ZeroDivisionError.
    ranked = reciprocal_rank_fusion([[a], [b, a]], k=0)
    assert [c.id for c in ranked] == [a.id, b.id]


def test_chat_search_closes_its_session_when_the_search_fails(monkeypatch):
    from sqlalchemy.orm import Session

    from app.retrieval import search

    def broken(*args, **kwargs):
        raise TimeoutError("statement timeout")

    monkeypatch.setattr(search, "search_for_chat", broken)
    closed = []
    reads = Session()
    monkeypatch.setattr(reads, "close", lambda: closed.append(True))

    with pytest.raises(TimeoutError):
        search.retrieve_for_chat(reads, uuid.uuid4(), "heap là gì")
    assert closed == [True]


def test_adaptive_k_largest_gap_handles_small_flat_and_buffered_distributions():
    chunks = [_make_chunk(f"c{i}") for i in range(10)]

    # n <= min_k returns n directly.
    assert _adaptive_k_largest_gap([(chunks[0], 0.9), (chunks[1], 0.2)], min_k=2) == 2

    # Flat scores (largest_gap <= 0): keep search_limit rather than cutting at index 0.
    flat = [(chunks[i], 0.5) for i in range(10)]
    assert _adaptive_k_largest_gap(flat, search_fraction=0.9, min_k=2) == 9

    # Sharp gap after 2nd item (index 1 gap = 0.65) with buffer=2 -> min(10, max(2, 2 + 2)) = 4.
    scored = [
        (chunks[0], 0.95),
        (chunks[1], 0.90),
        (chunks[2], 0.25),
        (chunks[3], 0.22),
        (chunks[4], 0.20),
        (chunks[5], 0.19),
    ]
    assert _adaptive_k_largest_gap(scored, buffer=2, search_fraction=0.9, min_k=2) == 4
    # Buffer large enough to hit n clamps to n.
    assert _adaptive_k_largest_gap(scored, buffer=10, search_fraction=0.9, min_k=2) == 6
    # min_k enforces lower bound even when cut + buffer is smaller.
    assert _adaptive_k_largest_gap(scored, buffer=0, search_fraction=0.9, min_k=4) == 4
    # Unclamped buffer=5 on 10 items: cut_at=3 (0.85 -> 0.30) + 5 = 8.
    ten = [
        (chunks[i], s)
        for i, s in enumerate([0.9, 0.88, 0.85, 0.3, 0.25, 0.2, 0.19, 0.18, 0.17, 0.16])
    ]
    assert _adaptive_k_largest_gap(ten, buffer=5) == 8


def test_adaptive_k_ignores_gaps_in_the_bottom_10_percent():
    # A huge artificial gap right at the tail (last item) should not be
    # picked if it falls outside the top 90% search window.
    scored = [(None, s) for s in [0.9] * 9 + [0.01]]
    result = _adaptive_k_largest_gap(scored, buffer=0, search_fraction=0.9, min_k=2)
    assert result >= 8


def test_a_refused_chat_query_falls_back_to_keywords_without_sleeping(monkeypatch):
    import litellm
    from litellm.exceptions import RateLimitError

    from app.ingestion import embeddings
    from app.retrieval import search

    calls = []

    def refuse(**kwargs):
        calls.append(kwargs)
        raise RateLimitError("limit", llm_provider="openai", model="embed-default")

    monkeypatch.setattr(litellm, "embedding", refuse)
    monkeypatch.setattr(embeddings.time, "sleep", lambda _: pytest.fail("a chat query must not wait"))
    monkeypatch.setattr(search, "sparse_search", lambda db, course_id, query, top_k: ["keyword hit"])

    assert search.search_for_chat(None, "course", "tiến trình là gì") == ["keyword hit"]
    assert len(calls) == 1 and calls[0]["timeout"] == embeddings.CHAT_TIMEOUT_SECONDS


def test_reranked_and_rerank_for_chat_and_scoped_filter(monkeypatch):
    import httpx

    from app.retrieval import search

    chunks = [_make_chunk(f"c{i}") for i in range(6)]
    monkeypatch.setattr(
        search,
        "rerank_scored",
        lambda query, cands, attempts=None: [
            (cands[0], 0.95),
            (cands[1], 0.92),
            (cands[2], 0.10),
            (cands[3], 0.08),
            (cands[4], 0.06),
            (cands[5], 0.05),
        ],
    )

    assert search._reranked("q", chunks, top_k=2, min_k=2) == chunks[:2]
    # Adaptive-k (top_k=None): default buffer=5 clamps 2 + 5 against n=6 -> 6.
    assert search._reranked("q", chunks, top_k=None, min_k=2) == chunks
    assert search.rerank_for_chat("q", chunks, top_k=3) == chunks[:3]

    # When rerank_scored fails with HTTP/ValueError, rerank_for_chat falls back to hybrid order.
    monkeypatch.setattr(search, "rerank_scored", lambda *a, **kw: (_ for _ in ()).throw(httpx.TimeoutException("slow")))
    assert search.rerank_for_chat("q", chunks, top_k=4) == chunks[:4]

    # _scoped leaves query untouched when scope is None, and adds filter when provided.
    sentinel = object()
    assert search._scoped(sentinel, None) is sentinel

    class FakeQuery:
        def __init__(self):
            self.clauses = None

        def filter(self, *clauses):
            self.clauses = clauses
            return self

    fq = FakeQuery()
    doc_id = uuid.uuid4()
    res = search._scoped(fq, search.ChunkScope(doc_id, first_chunk_index=2, last_chunk_index=7))
    assert res is fq and len(fq.clauses) == 2
