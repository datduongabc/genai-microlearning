"""The line a source picker shows for a passage: its own words, not the PDF's running header."""
from app.api.media_common import SNIPPET_CHARS, passage_snippet, repeated_head_lines

HEADER = "Operating System Concepts\nSilberschatz, Galvin and Gagne ©2018"
RR = ("Chapter 5 CPU Scheduling › Round Robin (RR) Scheduling\n19\n" + HEADER + "\nRound Robin (RR) Scheduling\n"
      "•Motivation: try scheduling algorithm similar to FCFS scheduling, but\npreemption is added to enable the system "
      "to switch between\nprocesses\n•Each process gets a small unit of CPU time (time quantum q), usually\n10-100 "
      "milliseconds. After this time has elapsed, the process is\npreempted and added to the end of the ready queue\n"
      "20\n" + HEADER + "\n•Timer interrupts every quantum to schedule next process")
SJF = "Chapter 5 CPU Scheduling\n15\n" + HEADER + "\n•A preemptive SJF algorithm will preempt the currently executing"
FCFS = ("Chapter 5 CPU Scheduling › FCFS Scheduling (Cont.)\n11\n" + HEADER + "\nFCFS Scheduling (Cont.)\n"
        "•Suppose that the processes arrive in the order: P2 , P3 , P1")
BOILERPLATE = repeated_head_lines([RR, SJF, FCFS], ["Chapter 5 CPU Scheduling › Round Robin (RR) Scheduling",
                                                    "Chapter 5 CPU Scheduling",
                                                    "Chapter 5 CPU Scheduling › FCFS Scheduling (Cont.)"])


def test_the_running_header_of_every_page_is_found():
    assert BOILERPLATE == {"Operating System Concepts", "Silberschatz, Galvin and Gagne ©2018"}


def test_lines_of_a_document_too_small_to_tell_are_not_boilerplate():
    assert repeated_head_lines([RR, FCFS], ["", ""]) == set()


def test_without_a_search_the_snippet_starts_with_the_passages_own_words():
    line = "Chapter 5 CPU Scheduling › FCFS Scheduling (Cont.)"
    assert passage_snippet(FCFS, line, BOILERPLATE, None) == (
        "FCFS Scheduling (Cont.) •Suppose that the processes arrive in the order: P2 , P3 , P1")
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
    # The page number and the header of the next page inside the passage are not shown either.
    assert "Silberschatz" not in passage_snippet(RR, line, BOILERPLATE, "Timer")


def test_a_search_matching_only_the_header_falls_back_to_the_start():
    snippet = passage_snippet(FCFS, "Chapter 5 CPU Scheduling › FCFS Scheduling (Cont.)", BOILERPLATE, "Galvin")
    assert snippet.startswith("FCFS Scheduling (Cont.)")


def test_a_passage_without_a_context_line_or_header_is_shown_as_it_is():
    assert passage_snippet("A heap is a tree.", "", set(), None) == "A heap is a tree."
    assert passage_snippet("A heap is a tree.", "", set(), "tree") == "A heap is a tree."
