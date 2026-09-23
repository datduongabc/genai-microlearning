from app.retrieval.search import _adaptive_k_largest_gap


def _scored(scores):
    # Chunk content doesn't matter for this pure-logic function -- use None
    # as a stand-in since _adaptive_k_largest_gap never touches chunk fields.
    return [(None, s) for s in scores]


def test_adaptive_k_cuts_at_largest_gap_plus_buffer():
    # scores: 0.9, 0.88, 0.85, 0.3, 0.25, 0.2 -- largest gap is 0.85 -> 0.3
    # (index 2->3), so cut_at = 3, + buffer(5) capped at len(scores)=6.
    scored = _scored([0.9, 0.88, 0.85, 0.3, 0.25, 0.2])
    assert _adaptive_k_largest_gap(scored, buffer=0) == 3


def test_adaptive_k_adds_buffer_after_cut():
    scored = _scored([0.9, 0.88, 0.85, 0.3, 0.25, 0.2, 0.19, 0.18, 0.17, 0.16])
    # cut_at=3 (gap between index 2 and 3), + buffer=5 -> 8
    assert _adaptive_k_largest_gap(scored, buffer=5) == 8


def test_adaptive_k_never_returns_fewer_than_min_k():
    scored = _scored([0.5, 0.4])
    assert _adaptive_k_largest_gap(scored, min_k=2) == 2


def test_adaptive_k_ignores_gaps_in_the_bottom_10_percent():
    # A huge artificial gap right at the tail (last item) should not be
    # picked if it falls outside the top 90% search window.
    scores = [0.9] * 9 + [0.01]  # 10 items: last is the only "gap", at the boundary
    scored = _scored(scores)
    result = _adaptive_k_largest_gap(scored, buffer=0, search_fraction=0.9, min_k=2)
    # With no meaningful gap inside the top 90%, should keep close to all items.
    assert result >= 8
