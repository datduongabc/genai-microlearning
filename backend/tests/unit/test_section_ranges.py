from app.ingestion.outline import OutlineEntry
from app.ingestion.section_ranges import assign_chunk_ranges, chunk_anchor


def _entries(*items):
    return [OutlineEntry(level=level, title=title, anchor=anchor) for level, title, anchor in items]


def test_a_section_runs_until_the_next_section_of_the_same_or_a_higher_level():
    entries = _entries((1, "Ch1", 1), (2, "1.1", 2), (2, "1.2", 4), (1, "Ch2", 6))
    anchors = [1, 1, 2, 3, 4, 5, 6, 7]

    specs = assign_chunk_ranges(entries, anchors, anchors_are_pages=True)

    assert {s.title: (s.first_chunk_index, s.last_chunk_index) for s in specs} == {
        "Ch1": (0, 5),
        "1.1": (2, 3),
        "1.2": (4, 5),
        "Ch2": (6, 7),
    }


def test_positions_follow_document_order_and_parents_are_the_nearest_shallower_section():
    entries = _entries((1, "Ch1", 1), (2, "1.1", 2), (2, "1.2", 4), (1, "Ch2", 6))

    specs = assign_chunk_ranges(entries, [1, 1, 2, 3, 4, 5, 6, 7], anchors_are_pages=True)

    assert [(s.position, s.title, s.parent_position) for s in specs] == [
        (0, "Ch1", None),
        (1, "1.1", 0),
        (2, "1.2", 0),
        (3, "Ch2", None),
    ]


def test_a_section_two_levels_down_still_hangs_under_the_nearest_shallower_one():
    entries = _entries((1, "Ch1", 1), (3, "1.0.1", 2), (1, "Ch2", 3))

    specs = assign_chunk_ranges(entries, [1, 2, 3], anchors_are_pages=True)

    assert [(s.title, s.level, s.parent_position) for s in specs] == [
        ("Ch1", 1, None),
        ("1.0.1", 3, 0),
        ("Ch2", 1, None),
    ]


def test_pdf_sections_get_page_ranges():
    entries = _entries((1, "Ch1", 1), (2, "1.1", 2), (2, "1.2", 4), (1, "Ch2", 6))

    specs = assign_chunk_ranges(entries, [1, 1, 2, 3, 4, 5, 6, 7], anchors_are_pages=True)

    assert {s.title: (s.page_start, s.page_end) for s in specs} == {
        "Ch1": (1, 5),
        "1.1": (2, 3),
        "1.2": (4, 5),
        "Ch2": (6, 7),
    }


def test_sections_of_unit_based_documents_have_no_pages():
    entries = _entries((1, "A", 0), (1, "B", 2))

    specs = assign_chunk_ranges(entries, [0, 1, 2, 3], anchors_are_pages=False)

    assert [(s.page_start, s.page_end) for s in specs] == [(None, None), (None, None)]
    assert [(s.first_chunk_index, s.last_chunk_index) for s in specs] == [(0, 1), (2, 3)]


def test_front_matter_before_the_first_section_belongs_to_no_section():
    entries = _entries((1, "Ch1", 3), (1, "Ch2", 5))

    specs = assign_chunk_ranges(entries, [1, 2, 3, 4, 5, 6], anchors_are_pages=True)

    assert specs[0].first_chunk_index == 2


def test_sections_that_end_up_with_no_chunk_are_dropped_and_positions_stay_contiguous():
    # "A" and "B" start on the same page, and chunks are assigned by the page
    # they start on, so the later section takes them and "A" has nothing left.
    entries = _entries((1, "A", 1), (1, "B", 1), (1, "C", 2))

    specs = assign_chunk_ranges(entries, [1, 2], anchors_are_pages=True)

    assert [(s.position, s.title) for s in specs] == [(0, "B"), (1, "C")]


def test_a_chunk_without_an_anchor_stays_with_the_chunk_before_it():
    entries = _entries((1, "Ch1", 1), (1, "Ch2", 3))

    specs = assign_chunk_ranges(entries, [1, None, 3, None], anchors_are_pages=True)

    assert {s.title: (s.first_chunk_index, s.last_chunk_index) for s in specs} == {
        "Ch1": (0, 1),
        "Ch2": (2, 3),
    }


def test_a_chunk_that_seems_to_start_before_the_one_ahead_of_it_stays_with_it():
    entries = _entries((1, "Ch1", 2), (1, "Ch2", 4))

    specs = assign_chunk_ranges(entries, [1, 3, 2, 4, 5], anchors_are_pages=True)

    assert {s.title: (s.first_chunk_index, s.last_chunk_index) for s in specs} == {
        "Ch1": (1, 2),
        "Ch2": (3, 4),
    }


def test_chunk_anchor_prefers_the_unit_index_then_the_first_page():
    assert chunk_anchor({"unit_index": 4, "heading": "x"}) == 4
    assert chunk_anchor({"page": 7}) == 7
    assert chunk_anchor({"spans": [{"page": 3}, {"page": 4}]}) == 3
    assert chunk_anchor({"start_ms": 0}) is None
