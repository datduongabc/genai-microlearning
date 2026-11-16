"""What a flashcard set and a video script share: the topics a focus names, the passages retrieved for
them, and the cleanup of a word the model wrote twice."""
import uuid
from types import SimpleNamespace

import pytest

from app.generation import media_common
from app.generation.media_common import MAX_FOCUS_CHUNKS, focus_topics, without_doubled_words


@pytest.mark.parametrize(("focus", "topics"), [
    ("FCFS và Round Robin", ["FCFS", "Round Robin"]),
    ("heaps and binary search trees", ["heaps", "binary search trees"]),
    ("FCFS, SJF; Round Robin", ["FCFS", "SJF", "Round Robin"]),
    ("So sánh FCFS vs SJF", ["So sánh FCFS", "SJF"]),
    # What follows a colon describes the topics, it is not one more.
    ("Lập lịch FCFS và Round Robin: cách chạy, biểu đồ Gantt", ["Lập lịch FCFS", "Round Robin"]),
    # A slash or a plus inside a name does not separate topics.
    ("I/O và C++", ["I/O", "C++"]),
])
def test_a_focus_naming_several_topics_is_split_into_them(focus, topics):
    assert focus_topics(focus) == topics


@pytest.mark.parametrize("focus", [
    "Round Robin",
    "tập trung vào array",
    # A part too short to be a topic of its own: the focus is one topic after all.
    "Ưu và nhược điểm của Round Robin",
    # Too many parts to be a list of topics: it is a description.
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
    # One query for "FCFS và Round Robin" ranked the Round Robin passages and others first and lost
    # FCFS's worked example; a query per topic finds both.
    fcfs, rr, shared = [_chunk(5), _chunk(4)], [_chunk(9), _chunk(10)], _chunk(23)
    db, doc, item, queries = _setup(monkeypatch, "FCFS và Round Robin",
                                    {"FCFS": [shared, *fcfs], "Round Robin": [shared, *rr]})
    found = media_common.focus_passages(db, doc, item)
    assert [q for q, _ in queries] == ["FCFS", "Round Robin"]
    assert all(kw["top_k"] is None and kw["scope"].document_id == doc.id for _, kw in queries)
    assert found.query == "FCFS và Round Robin" and found.topics == ["FCFS", "Round Robin"]
    # Once each, in reading order, so the passages read as the lecture does.
    assert [c.chunk_index for c in found.chunks] == [4, 5, 9, 10, 23]


def test_the_merged_passages_are_capped_taking_from_every_topic_in_turn(monkeypatch):
    a = [_chunk(i) for i in range(20)]
    b = [_chunk(i) for i in range(100, 120)]
    db, doc, item, _ = _setup(monkeypatch, "Alpha và Beta", {"Alpha": a, "Beta": b})
    found = media_common.focus_passages(db, doc, item)
    assert len(found.chunks) == MAX_FOCUS_CHUNKS
    # Each topic keeps its best passages: neither is crowded out by the other.
    indexes = {c.chunk_index for c in found.chunks}
    assert indexes == set(range(MAX_FOCUS_CHUNKS // 2)) | set(range(100, 100 + MAX_FOCUS_CHUNKS // 2))


def test_a_focus_of_one_topic_is_one_query(monkeypatch):
    chunks = [_chunk(3), _chunk(1)]
    db, doc, item, queries = _setup(monkeypatch, "Round Robin", {"Round Robin": chunks})
    found = media_common.focus_passages(db, doc, item)
    assert [q for q, _ in queries] == ["Round Robin"]
    # The ranking of a single query is kept as it is.
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
    # Words Vietnamese doubles on purpose, and figures, are left alone.
    ("tăng dần dần, luôn luôn, từ từ", "tăng dần dần, luôn luôn, từ từ"),
    ("P2 3 3", "P2 3 3"),
    # Two separate words that only look alike.
    ("việc Việt", "việc Việt"),
])
def test_a_word_written_twice_in_a_row_is_written_once(text, clean):
    assert without_doubled_words(text) == clean
