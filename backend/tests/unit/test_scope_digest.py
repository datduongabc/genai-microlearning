from app.generation.scope_digest import build_scope_digest


def test_a_short_section_is_passed_through_whole():
    assert build_scope_digest(["one", "two"], budget=100) == "one\n\ntwo"


def test_blank_chunks_are_ignored_and_no_text_gives_an_empty_digest():
    assert build_scope_digest(["", "   "], budget=100) == ""
    assert build_scope_digest([], budget=100) == ""


def test_a_long_section_fits_the_budget_and_is_deterministic():
    texts = [f"CHUNK-{i} " + "x" * 900 for i in range(100)]

    first = build_scope_digest(texts, budget=8000)

    assert len(first) <= 8000
    assert first == build_scope_digest(texts, budget=8000)


def test_a_long_section_is_sampled_across_its_whole_length():
    texts = [f"CHUNK-{i} " + "x" * 900 for i in range(100)]

    digest = build_scope_digest(texts, budget=8000)

    assert "CHUNK-0 " in digest
    assert "CHUNK-99 " in digest
    assert any(f"CHUNK-{i} " in digest for i in range(30, 70))


def test_a_section_with_few_but_long_chunks_still_fits_the_budget():
    texts = ["a" * 5000, "b" * 5000, "c" * 5000]

    digest = build_scope_digest(texts, budget=8000)

    assert len(digest) <= 8000
    assert {"a", "b", "c"} <= set(digest)
