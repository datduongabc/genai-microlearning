"""Which lines of a PDF start a numbered section. Every line below was read from a real lecture
PDF: the two handouts uploaded on 2026-11-11 and the five-subject evaluation corpus."""

import pytest

from app.ingestion.document_readers import is_numbered_heading


@pytest.mark.parametrize("line", [
    "1 Introduction to Syntax Analysis",
    "2 Context-free grammar",
    "1 Khái niệm dãy số",
    "3 Giới hạn của dãy số",
    "2.3 Lexical rules",
    "1.2. Biểu thức chính quy",
])
def test_section_headings(line):
    assert is_numbered_heading(line)


@pytest.mark.parametrize("line", [
    # The page counter of a handout, on every page: the source label read "4 / 33, trang 3–4".
    "4 / 33",
    "33 / 33",
    # Code and data.
    "100 INT_LIT",
    "65000 words",
    "0 => { 0 }",
    "0 | 1 => {0,1}",
    "9 - 5 - 2 ⇒6 ⇒2",
    "1 0 . 0 e 2 0 . . . Input Tape",
    # Items of a numbered list are sentences, not sections.
    "1 Nếu α > 1 thì I = ∫_a^(+∞) dx/x^α hội tụ.",
    "1 Nếu f (x), g(x) là những VCB tương đương khi",
    "1. Tính tăng và tính giảm.",
    "2 Replace any non-terminal symbol X in the string with the",
    "1 Theo kết quả điều tra dân số năm 2000 thì",
    "1. Mutual Exclusion – If process P_i is executing in its critical section",
    # A sentence that starts with a number.
    "630 m. Phương trình của cổng chào là",
    "3 tokens are returned",
])
def test_not_headings(line):
    assert not is_numbered_heading(line)
