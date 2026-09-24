from app.ingestion.chunker import _classify, chunk_units, document_title
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
    # Dòng đầu là tiêu đề mục làm nhãn ngữ cảnh, phần còn lại là nguyên văn đoạn chữ.
    assert chunks[0].content == "H\nShort text."


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


# --- nhãn ngữ cảnh -----------------------------------------------------------


def test_document_title_is_the_file_name_without_extension_or_underscores():
    assert document_title("Chapter 1_Introduction to OS.pdf") == "Chapter 1 Introduction to OS"
    assert document_title("uploads/abc/Chương 4a - Tích phân bất định.pdf") == "Chương 4a - Tích phân bất định"


def test_every_chunk_of_a_titled_document_starts_with_the_document_title():
    units = [
        DocumentUnit(text=f"Slide {i} body text", locator={"page": i, "heading": f"Slide {i}"}, course_id="c")
        for i in range(1, 6)
    ]

    chunks = chunk_units(units, title="Heap")

    assert chunks and all(c.content.splitlines()[0].startswith("Heap") for c in chunks)


def test_a_chunk_that_does_not_open_with_its_heading_gets_the_heading_in_its_context_line():
    units = [DocumentUnit(text="∫ x dx = x^2/2 + C", locator={"page": 3, "heading": "Bảng tích phân"}, course_id="c")]

    chunks = chunk_units(units, title="Tích phân bất định")

    assert chunks[0].content.splitlines()[0] == "Tích phân bất định › Bảng tích phân"


def test_a_chunk_that_already_opens_with_its_heading_does_not_repeat_it():
    units = [DocumentUnit(text="Bảng tích phân\n∫ x dx", locator={"page": 3, "heading": "Bảng tích phân"}, course_id="c")]

    chunks = chunk_units(units, title="Tích phân bất định")

    assert chunks[0].content.splitlines()[:2] == ["Tích phân bất định", "Bảng tích phân"]


def test_without_a_title_or_heading_the_content_is_unchanged():
    units = [DocumentUnit(text="plain text", locator={"page": 1}, course_id="c")]

    assert chunk_units(units)[0].content == "plain text"


def test_slide_titles_label_their_chunks():
    units = [DocumentUnit(text="body only", locator={"slide_index": 2, "slide_title": "Encapsulation"}, course_id="c")]

    assert chunk_units(units, title="OOP")[0].content.splitlines()[0] == "OOP › Encapsulation"


def test_recursive_chunks_are_labelled_with_the_section_they_come_from_not_the_first_one():
    units = [
        DocumentUnit(text="A" * 2500, locator={"heading": "Chapter 1"}, course_id="c"),
        DocumentUnit(text="B" * 2500, locator={"heading": "Chapter 2"}, course_id="c"),
    ]

    chunks = chunk_units(units, title="Book")

    last = chunks[-1]
    assert "B" in last.content and last.content.splitlines()[0] == "Book › Chapter 2"
