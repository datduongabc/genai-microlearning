"""Unit tests for inline citation derivation and character-offset invariants."""

import types
import uuid

import pytest

from app.generation.inline_citations import (
    citation_content,
    derive_citations,
    prompt_source_map,
    unknown_source_codes,
)


def _chunk(locator='{"page": 1}'):
    return types.SimpleNamespace(id=uuid.uuid4(), content="c", source_ref=locator)


def test_prompt_source_map_and_unknown_source_codes_catch_invented_and_malformed_codes():
    chunks = [_chunk('{"page": 1}'), _chunk("")]
    smap = prompt_source_map(chunks)

    assert list(smap) == ["S1", "S2"]
    assert smap["S1"].chunk_id == str(chunks[0].id)
    assert smap["S2"].locator == ""

    md = "Valid [S1]. Bad [S9]. Case [s1]. Multi [S1, S2]. Dup [S9]."
    assert unknown_source_codes(md, smap) == ["S9", "[s1]", "[S1, S2]"]


def test_derive_citations_slices_last_sentence_strips_decorations_and_keeps_exact_offsets():
    chunks = [_chunk('{"heading": "A"}'), _chunk('{"heading": "B"}')]
    smap = prompt_source_map(chunks)

    md = (
        "Uncited lead. Second claim! [S1]\n"
        "- Bullet item [S1][S2]\n"
        "### Heading claim [S2]\n"
        "1) Numbered claim [S1]\n"
        "1. First item. Actual claim [S2]"
    )
    cited = derive_citations(md, smap)

    assert len(cited) == 6
    # First claim must start after "Uncited lead. " and include "Second claim!".
    assert cited[0].claim == "Second claim!"
    assert md[cited[0].start : cited[0].end] == "Second claim!"
    assert md[cited[0].marker_start : cited[0].marker_end] == "[S1]"
    assert cited[0].source_id == "S1"

    # Adjacent markers [S1][S2] share the stripped bullet text without "- ".
    assert cited[1].claim == "Bullet item"
    assert cited[2].claim == "Bullet item"
    assert (cited[1].start, cited[1].end) == (cited[2].start, cited[2].end)
    assert [cited[1].source_id, cited[2].source_id] == ["S1", "S2"]

    # Heading prefix "### ", numbered prefix "1) ", and mid-line sentence split after "1. First item. "
    assert cited[3].claim == "Heading claim"
    assert cited[4].claim == "Numbered claim"
    assert cited[5].claim == "Actual claim"
    assert md[cited[3].start : cited[3].end] == "Heading claim"
    assert md[cited[4].start : cited[4].end] == "Numbered claim"
    assert md[cited[5].start : cited[5].end] == "Actual claim"


def test_derive_citations_rejects_empty_claim_and_citation_content_deduplicates_locators():
    chunks = [_chunk('{"p": 1}'), _chunk('{"p": 1}'), _chunk("")]
    smap = prompt_source_map(chunks)

    for bare in ("[S1]", "   [S1]", "First. [S1]\n   [S2]"):
        with pytest.raises(ValueError, match="must follow a sentence or bullet"):
            derive_citations(bare, smap)

    with pytest.raises(ValueError, match="unknown source IDs: S8"):
        derive_citations("Fact [S8]", smap)

    payload = citation_content("Claim A. [S1] Claim B. [S2] Claim C. [S3]", smap)
    assert payload["markdown"].startswith("Claim A.")
    assert len(payload["inline_citations"]) == 3
    # Shared non-empty locator deduplicated; empty locator omitted.
    assert payload["source_locators"] == ['{"p": 1}']
    assert set(payload["source_map"]) == {"S1", "S2", "S3"}
