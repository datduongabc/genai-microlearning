"""Which failures are an outage of a service we depend on, and how long the queue waits after one."""

import threading
import uuid
from types import SimpleNamespace

import httpx
import pytest
from litellm.exceptions import APIConnectionError, InternalServerError, RateLimitError
from sqlalchemy.exc import OperationalError

from app.jobs import queue
from app.jobs.failures import SYSTEM_FAILURE_MESSAGE, status_error
from app.models import JobStatus


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://reranker/rerank")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(code, request=request))


@pytest.mark.parametrize("err", [
    httpx.ConnectError("reranker down"),
    _status_error(503),
    OperationalError("SELECT 1", {}, Exception("terminating connection due to administrator command")),
    APIConnectionError(message="Connection error.", llm_provider="openai", model="m"),
    InternalServerError(message="Internal Server Error", llm_provider="openai", model="m"),
])
def test_an_unavailable_service_is_an_outage(err):
    assert queue.is_outage(err)


def test_a_wrapped_connection_error_is_found_through_its_cause():
    try:
        try:
            raise httpx.ConnectError("embedder down")
        except httpx.ConnectError as inner:
            raise RuntimeError("embedding failed") from inner
    except RuntimeError as outer:
        assert queue.is_outage(outer)


@pytest.mark.parametrize("err", [
    ValueError("bad data"),
    _status_error(400),
    RateLimitError(message="slow down", llm_provider="openai", model="m"),
    RuntimeError("a bug"),
])
def test_other_failures_are_not(err):
    assert not queue.is_outage(err)


def test_an_outage_waits_long_enough_to_ride_out_minutes(monkeypatch):
    monkeypatch.setattr(queue.random, "random", lambda: 0.5)  # no jitter
    outage = sum(queue.jittered_backoff(n, queue.OUTAGE_BASE_BACKOFF_SECONDS) for n in range(1, 5))
    usual = sum(queue.jittered_backoff(n) for n in range(1, 5))
    assert usual == 75
    # Four retries before the fifth attempt fails the job for good.
    assert outage == 450


def test_five_missed_beats_make_a_job_stuck():
    assert queue.STUCK_THRESHOLD_SECONDS == 5 * queue.HEARTBEAT_INTERVAL_SECONDS


def test_a_failed_beat_is_retried_soon(monkeypatch):
    """A worker whose database came back must refresh its lock before the sweeper, which waited too, takes it."""
    waits, beats = [], []
    stop = threading.Event()

    def fake_wait(self, timeout=None):
        waits.append(timeout)
        return len(waits) > 3

    def beat(job_id, worker_id):
        beats.append(1)
        if len(beats) == 1:
            raise OperationalError("UPDATE", {}, Exception("connection refused"))
        return True

    monkeypatch.setattr(threading.Event, "wait", fake_wait)
    monkeypatch.setattr(queue, "heartbeat", beat)

    queue._beat_until(stop, uuid.uuid4(), "w")

    assert waits[:3] == [queue.HEARTBEAT_INTERVAL_SECONDS, queue.HEARTBEAT_RETRY_SECONDS,
                         queue.HEARTBEAT_INTERVAL_SECONDS]


RAW_FAILURE = "ConnectError: http://gateway:4000/v1/embeddings refused"


def _job(status, system_failure, last_error=RAW_FAILURE):
    return SimpleNamespace(status=status, system_failure=system_failure, last_error=last_error)


def test_a_job_waiting_for_its_next_try_shows_no_error():
    assert status_error(_job(JobStatus.queued, False)) is None
    assert status_error(_job(JobStatus.running, False)) is None


def test_a_system_failure_shows_the_general_message():
    assert status_error(_job(JobStatus.failed, True)) == SYSTEM_FAILURE_MESSAGE


def test_a_failure_caused_by_the_input_shows_its_message():
    assert status_error(_job(JobStatus.failed, False, "Tài liệu không có chữ.")) == "Tài liệu không có chữ."
