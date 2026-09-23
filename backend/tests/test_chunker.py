from app.ingestion.chunker import _classify, chunk_units
from app.ingestion.document_readers import DocumentUnit


def _unit(text, locator, course_id="c1", source_id="doc1"):
    return DocumentUnit(text=text, locator=locator, course_id=course_id, source_id=source_id)


def test_classify_page_level_for_pdf_locator():
    assert _classify(_unit("x", {"page": 1})) == "page_level"


def test_classify_page_level_for_slide_locator():
    assert _classify(_unit("x", {"slide_index": 1})) == "page_level"


def test_classify_sentence_for_transcript_locator():
    assert _classify(_unit("x", {"start_ms": 0, "end_ms": 100})) == "sentence"


def test_classify_recursive_default():
    assert _classify(_unit("x", {"heading": "H"})) == "recursive"


def test_chunk_units_recursive_short_text_stays_one_chunk():
    units = [_unit("Short text.", {"heading": "H"})]
    chunks = chunk_units(units)
    assert len(chunks) == 1
    assert chunks[0].strategy == "recursive"
    assert chunks[0].content == "Short text."


def test_chunk_units_page_level_groups_within_cap():
    units = [
        _unit("Page one text.", {"page": 1, "extraction_method": "text_layer"}),
        _unit("Page two text.", {"page": 2, "extraction_method": "text_layer"}),
        _unit("Page three text.", {"page": 3, "extraction_method": "text_layer"}),
    ]
    chunks = chunk_units(units)
    # MAX_UNITS_PER_CHUNK = 2 -> pages 1-2 group together, page 3 starts a new chunk.
    assert len(chunks) == 2
    assert all(c.strategy == "page_level" for c in chunks)


def test_chunk_units_page_level_single_non_text_layer_method_surfaced():
    # A chunk merged from a text_layer page + an OCR page must surface "ocr",
    # not silently inherit "text_layer" from the first page (real gap found
    # live -- this is exactly the quality signal MAX_UNITS_PER_CHUNK protects).
    units = [
        _unit("ok text", {"page": 1, "extraction_method": "text_layer"}),
        _unit("ocr text", {"page": 2, "extraction_method": "ocr"}),
    ]
    chunks = chunk_units(units)
    assert len(chunks) == 1
    assert chunks[0].extraction_method == "ocr"


def test_chunk_units_page_level_two_distinct_non_text_layer_methods_flagged_mixed():
    # "mixed" is reserved for genuine ambiguity between two different
    # non-text_layer methods (e.g. one page OCR'd cleanly, another OCR failed).
    units = [
        _unit("ocr text", {"page": 1, "extraction_method": "ocr"}),
        _unit("garbled", {"page": 2, "extraction_method": "ocr_failed"}),
    ]
    chunks = chunk_units(units)
    assert len(chunks) == 1
    assert chunks[0].extraction_method == "mixed"


def test_chunk_units_transcript_oversized_single_unit_is_split():
    # Regression test: a single caption/segment far larger than the target
    # (e.g. a mis-segmented ASR transcript dumping an entire lecture into one
    # 2-second "caption") must still be split, not emitted as one giant
    # chunk -- real bug found via eng-introduction.srt sample data.
    long_text = "word " * 300  # well over TRANSCRIPT_CHUNK_CHARS=600
    units = [_unit(long_text, {"start_ms": 0, "end_ms": 2000})]
    chunks = chunk_units(units)
    assert len(chunks) > 1
    assert all(c.strategy == "sentence" for c in chunks)


def test_chunk_units_transcript_merges_short_segments_and_keeps_time_range():
    units = [
        _unit("Hello.", {"start_ms": 0, "end_ms": 500}),
        _unit("World.", {"start_ms": 500, "end_ms": 1000}),
    ]
    chunks = chunk_units(units)
    assert len(chunks) == 1
    assert chunks[0].locator == {"start_ms": 0, "end_ms": 1000}


def test_recursive_chunks_record_the_unit_they_start_in():
    units = [
        _unit("alpha " * 500, {"heading": "A", "heading_level": 1}),
        _unit("beta " * 500, {"heading": "B", "heading_level": 1}),
    ]

    chunks = chunk_units(units)

    indexes = [c.locator["unit_index"] for c in chunks]
    assert len(chunks) > 2
    assert indexes == sorted(indexes)
    assert indexes[0] == 0
    assert indexes[-1] == 1
    assert set(indexes) == {0, 1}


def test_unit_index_counts_positions_in_the_original_list_even_when_units_are_empty():
    units = [
        _unit("", {"heading": "empty", "heading_level": 1}),
        _unit("real text", {"heading": "real", "heading_level": 1}),
    ]

    chunks = chunk_units(units)

    assert [c.locator["unit_index"] for c in chunks] == [1]
