import uuid

import pytest

from app.models import Chunk
from app.retrieval.search import _tokenize, reciprocal_rank_fusion


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
    fused = reciprocal_rank_fusion([[a], [a]])
    assert len(fused) == 1


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
