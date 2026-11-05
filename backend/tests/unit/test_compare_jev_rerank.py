"""The Jev comparison: a wrong order or a re-sent paid call would not fail visibly, only skew the result."""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import compare_jev_rerank as jev


def _client(tmp_path, handler) -> jev.JevClient:
    return jev.JevClient("k", tmp_path / "cache.jsonl", httpx.MockTransport(handler))


def _answers(request: httpx.Request, score) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(200, json={
        "model": "jev-1.14.0", "usage": {"input_tokens": 100, "output_tokens": 1},
        "answers": {name: {"type": "noul", "noul": score(body, name)} for name in body["questions"]}})


def test_pair_sorts_by_probability_and_keeps_search_order_on_ties(tmp_path):
    scores = {"a": 0.2, "b": 0.9, "c": 0.2}
    client = _client(tmp_path, lambda r: _answers(r, lambda body, _: scores[body["state"]["passage"]]))

    scores, tokens, _ = jev.jev_pair(client, "q", ["a", "b", "c"])

    assert jev._order(scores) == [1, 0, 2]
    assert tokens == 300


def test_batch_reads_each_answer_by_its_own_name(tmp_path):
    scores = {"p0": 0.1, "p1": 0.5, "p2": 0.8}
    client = _client(tmp_path, lambda r: _answers(r, lambda _, name: scores[name]))

    scores, tokens, _ = jev.jev_batch(client, "q", ["a", "b", "c"])

    assert jev._order(scores) == [2, 1, 0]
    assert tokens == 100


def test_repeated_question_is_not_paid_again_and_cache_holds_no_course_text(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return _answers(request, lambda *_: 0.5)

    client = _client(tmp_path, handler)
    question = {"relevant": {"type": "noul", "instructions": "x"}}
    client.ask({"query": "q", "passage": "private course text"}, question)
    again = _client(tmp_path, handler).ask({"query": "q", "passage": "private course text"}, question)

    assert len(calls) == 1
    assert again["seconds"] == 0.0
    assert again["nouls"] == {"relevant": 0.5}
    assert "private course text" not in (tmp_path / "cache.jsonl").read_text()


def test_rate_limit_is_retried_and_a_bad_request_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(jev.time, "sleep", lambda _: None)
    statuses = iter([429, 529, 200])


    def flaky(request):
        status = next(statuses)
        return _answers(request, lambda *_: 0.5) if status == 200 else httpx.Response(status)

    assert _client(tmp_path, flaky).ask({"query": "q"}, {"a": {}})["nouls"] == {"a": 0.5}

    calls = []
    bad = _client(tmp_path, lambda r: (calls.append(r), httpx.Response(400))[1])
    with pytest.raises(httpx.HTTPStatusError):
        bad.ask({"query": "other"}, {"a": {"type": "noul", "instructions": "x"}})
    assert len(calls) == 1


def test_dev_and_test_do_not_overlap_and_share_each_subject_evenly():
    dev = [i for i in range(250) if jev.in_subset(i, "dev")]
    test = [i for i in range(250) if jev.in_subset(i, "test")]

    assert len(dev) == 50
    assert len(test) == 200
    assert not set(dev) & set(test)
    assert [sum(i // 50 == subject for i in dev) for subject in range(5)] == [10] * 5


def test_adaptive_k_cuts_at_the_largest_gap_and_keeps_the_floor():
    # Sorted: 0.9, 0.9, 0.1, ... The largest gap follows the second chunk, and five more stay as a buffer.
    scores = [0.1] * 8 + [0.9, 0.9]

    assert jev.adaptive_k(scores, min_k=4) == 7
    assert jev.adaptive_k(scores, min_k=8) == 8
