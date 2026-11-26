"""Unit tests for media source passage snippets and shared media focus retrieval/cleanup."""
import uuid
from types import SimpleNamespace

import pytest

from app.api.media_common import SNIPPET_CHARS, passage_snippet, repeated_head_lines
from app.generation import media_common
from app.generation.media_common import MAX_FOCUS_CHUNKS, focus_topics, without_doubled_words

HEADER = "Operating System Concepts\nSilberschatz, Galvin and Gagne ©2018"
RR = (
    "Chapter 5 CPU Scheduling › Round Robin (RR) Scheduling\n19\n" + HEADER + "\nRound Robin (RR) Scheduling\n"
    "•Motivation: try scheduling algorithm similar to FCFS scheduling, but\npreemption is added to enable the system "
    "to switch between\nprocesses\n•Each process gets a small unit of CPU time (time quantum q), usually\n10-100 "
    "milliseconds. After this time has elapsed, the process is\npreempted and added to the end of the ready queue\n"
    "20\n" + HEADER + "\n•Timer interrupts every quantum to schedule next process"
)
SJF = "Chapter 5 CPU Scheduling\n15\n" + HEADER + "\n•A preemptive SJF algorithm will preempt the currently executing"
FCFS = (
    "Chapter 5 CPU Scheduling › FCFS Scheduling (Cont.)\n11\n" + HEADER + "\nFCFS Scheduling (Cont.)\n"
    "•Suppose that the processes arrive in the order: P2 , P3 , P1"
)
BOILERPLATE = repeated_head_lines(
    [RR, SJF, FCFS],
    [
        "Chapter 5 CPU Scheduling › Round Robin (RR) Scheduling",
        "Chapter 5 CPU Scheduling",
        "Chapter 5 CPU Scheduling › FCFS Scheduling (Cont.)",
    ],
)


def test_the_running_header_of_every_page_is_found():
    assert BOILERPLATE == {"Operating System Concepts", "Silberschatz, Galvin and Gagne ©2018"}


def test_lines_of_a_document_too_small_to_tell_are_not_boilerplate():
    assert repeated_head_lines([RR, FCFS], ["", ""]) == set()


def test_without_a_search_the_snippet_starts_with_the_passages_own_words():
    line = "Chapter 5 CPU Scheduling › FCFS Scheduling (Cont.)"
    assert passage_snippet(FCFS, line, BOILERPLATE, None) == (
        "FCFS Scheduling (Cont.) •Suppose that the processes arrive in the order: P2 , P3 , P1"
    )
    assert passage_snippet(SJF, "Chapter 5 CPU Scheduling", BOILERPLATE, None).startswith("•A preemptive SJF")


def test_with_a_search_the_snippet_is_built_around_the_first_match():
    line = "Chapter 5 CPU Scheduling › Round Robin (RR) Scheduling"
    snippet = passage_snippet(RR, line, BOILERPLATE, "TIME QUANTUM")
    assert "time quantum q" in snippet
    assert snippet.startswith("…") and len(snippet) <= SNIPPET_CHARS
    assert "Silberschatz" not in snippet


def test_a_long_passage_is_cut_at_a_word_with_an_ellipsis():
    line = "Chapter 5 CPU Scheduling › Round Robin (RR) Scheduling"
    snippet = passage_snippet(RR, line, BOILERPLATE, None)
    assert len(snippet) <= SNIPPET_CHARS and snippet.endswith("…")
    assert snippet.startswith("Round Robin (RR) Scheduling •Motivation")
    assert "Silberschatz" not in passage_snippet(RR, line, BOILERPLATE, "Timer")


def test_a_search_matching_only_the_header_falls_back_to_the_start():
    snippet = passage_snippet(FCFS, "Chapter 5 CPU Scheduling › FCFS Scheduling (Cont.)", BOILERPLATE, "Galvin")
    assert snippet.startswith("FCFS Scheduling (Cont.)")


def test_a_passage_without_a_context_line_or_header_is_shown_as_it_is():
    assert passage_snippet("A heap is a tree.", "", set(), None) == "A heap is a tree."
    assert passage_snippet("A heap is a tree.", "", set(), "tree") == "A heap is a tree."


@pytest.mark.parametrize(("focus", "topics"), [
    ("FCFS và Round Robin", ["FCFS", "Round Robin"]),
    ("heaps and binary search trees", ["heaps", "binary search trees"]),
    ("FCFS, SJF; Round Robin", ["FCFS", "SJF", "Round Robin"]),
    ("So sánh FCFS vs SJF", ["So sánh FCFS", "SJF"]),
    ("Lập lịch FCFS và Round Robin: cách chạy, biểu đồ Gantt", ["Lập lịch FCFS", "Round Robin"]),
    ("I/O và C++", ["I/O", "C++"]),
])
def test_a_focus_naming_several_topics_is_split_into_them(focus, topics):
    assert focus_topics(focus) == topics


@pytest.mark.parametrize("focus", [
    "Round Robin",
    "tập trung vào array",
    "Ưu và nhược điểm của Round Robin",
    "queues, stacks, heaps, trees, graphs",
    "",
])
def test_a_focus_naming_one_topic_is_not_split(focus):
    assert focus_topics(focus) == []


def _chunk(index):
    return SimpleNamespace(id=uuid.uuid4(), chunk_index=index, content=f"passage {index}")


def _setup(monkeypatch, focus, by_query):
    doc = SimpleNamespace(id=uuid.uuid4(), course_id=uuid.uuid4())
    item = SimpleNamespace(focus=focus, output_language="vi")
    queries = []

    def fake(_db, _course, query, **kw):
        queries.append((query, kw))
        return by_query[query]

    monkeypatch.setattr(media_common, "retrieve", fake)
    db = SimpleNamespace(commit=lambda: None)
    return db, doc, item, queries


def test_each_named_topic_is_retrieved_on_its_own_and_merged(monkeypatch):
    fcfs, rr, shared = [_chunk(5), _chunk(4)], [_chunk(9), _chunk(10)], _chunk(23)
    db, doc, item, queries = _setup(
        monkeypatch, "FCFS và Round Robin", {"FCFS": [shared, *fcfs], "Round Robin": [shared, *rr]}
    )
    found = media_common.focus_passages(db, doc, item)
    assert [q for q, _ in queries] == ["FCFS", "Round Robin"]
    assert all(kw["top_k"] is None and kw["scope"].document_id == doc.id for _, kw in queries)
    assert found.query == "FCFS và Round Robin" and found.topics == ["FCFS", "Round Robin"]
    assert [c.chunk_index for c in found.chunks] == [4, 5, 9, 10, 23]


def test_the_merged_passages_are_capped_taking_from_every_topic_in_turn(monkeypatch):
    a = [_chunk(i) for i in range(20)]
    b = [_chunk(i) for i in range(100, 120)]
    db, doc, item, _ = _setup(monkeypatch, "Alpha và Beta", {"Alpha": a, "Beta": b})
    found = media_common.focus_passages(db, doc, item)
    assert len(found.chunks) == MAX_FOCUS_CHUNKS
    indexes = {c.chunk_index for c in found.chunks}
    assert indexes == set(range(MAX_FOCUS_CHUNKS // 2)) | set(range(100, 100 + MAX_FOCUS_CHUNKS // 2))


def test_a_focus_of_one_topic_is_one_query(monkeypatch):
    chunks = [_chunk(3), _chunk(1)]
    db, doc, item, queries = _setup(monkeypatch, "Round Robin", {"Round Robin": chunks})
    found = media_common.focus_passages(db, doc, item)
    assert [q for q, _ in queries] == ["Round Robin"]
    assert found.chunks == chunks and found.topics == []


def test_the_planners_topic_is_never_split(monkeypatch):
    concepts = [SimpleNamespace(name="FCFS", summary="runs jobs in order, and is simple", priority=1)]
    monkeypatch.setattr(media_common, "_get_or_cache_planner", lambda *a, **k: SimpleNamespace(concepts=concepts))
    query = "FCFS: runs jobs in order, and is simple"
    db, doc, item, queries = _setup(monkeypatch, None, {query: [_chunk(1)]})
    found = media_common.focus_passages(db, doc, item)
    assert [q for q, _ in queries] == [query] and found.topics == []


@pytest.mark.parametrize(("text", "clean"), [
    ("ngoại trừ việc việc chia sẻ thời gian", "ngoại trừ việc chia sẻ thời gian"),
    ("Việc việc chia sẻ", "Việc chia sẻ"),
    ("the the queue", "the queue"),
    ("chờ chờ chờ lâu", "chờ lâu"),
    ("tăng dần dần, luôn luôn, từ từ", "tăng dần dần, luôn luôn, từ từ"),
    ("P2 3 3", "P2 3 3"),
    ("việc Việt", "việc Việt"),
])
def test_a_word_written_twice_in_a_row_is_written_once(text, clean):
    assert without_doubled_words(text) == clean
