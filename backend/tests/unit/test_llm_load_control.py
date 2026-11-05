"""A rate limit waits inside the one call, so it does not restart a whole job."""

from types import SimpleNamespace

import httpx
import litellm
import pytest
from pydantic import BaseModel

from app import timing
from app.config import settings
from app.generation.slides import Slide, SlidesOutput
from app.jobs import queue
from app.llm import gateway


def _rate_limit(message="slow down", headers=None):
    response = httpx.Response(429, headers=headers or {}, request=httpx.Request("POST", "http://gateway.test"))
    return litellm.RateLimitError(message=message, llm_provider="openai", model="m", response=response)


def _reply():
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=usage, model="m")


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    waits = []
    monkeypatch.setattr(gateway.time, "sleep", waits.append)
    monkeypatch.setattr(settings, "llm_rate_limit_retries", 3)
    token = gateway._waits_on_rate_limit.set(True)
    yield waits
    gateway._waits_on_rate_limit.reset(token)


def test_a_rate_limit_is_retried_inside_the_call_and_recorded(monkeypatch, no_waiting):
    replies = [_rate_limit(), _rate_limit(), _reply()]

    def fake(**_):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(litellm, "completion", fake)
    with timing.capture_timings() as spans:
        assert gateway.complete("hello") == "ok"

    assert not replies
    assert len(no_waiting) == 2
    assert no_waiting[1] > no_waiting[0] * 0.6
    assert sum(s.get("rate_limited", False) for s in spans) == 2
    assert sum(s["kind"] == "llm_backoff" for s in spans) == 2


def test_the_providers_retry_after_is_followed_up_to_the_limit():
    assert 2.0 <= gateway._rate_limit_wait(_rate_limit(headers={"retry-after": "2"}), 0) <= 3.0
    assert gateway._rate_limit_wait(_rate_limit(headers={"retry-after": "9000"}), 0) <= 90.0


def test_it_gives_up_after_the_configured_retries(monkeypatch, no_waiting):
    calls = []

    def fake(**_):
        calls.append(1)
        raise _rate_limit()

    monkeypatch.setattr(litellm, "completion", fake)
    with pytest.raises(litellm.RateLimitError):
        gateway.complete("hello")
    assert len(calls) == 4


@pytest.mark.parametrize("message", ["insufficient_quota", "Your credit balance is too low", "billing_hard_limit_reached", "You have no credits remaining"])
def test_running_out_of_money_is_not_waited_on(monkeypatch, no_waiting, message):
    calls = []

    def fake(**_):
        calls.append(1)
        raise _rate_limit(message)

    monkeypatch.setattr(litellm, "completion", fake)
    with pytest.raises(litellm.RateLimitError):
        gateway.complete("hello")
    assert len(calls) == 1
    assert not no_waiting


def test_a_slide_over_the_limit_is_cut_instead_of_failing_the_module():
    assert len(Slide(title="t", bullets=["a", "b", "c", "d"]).bullets) == 3
    many = [{"title": "t", "bullets": ["a"]} for _ in range(10)]
    assert len(SlidesOutput(slides=many).slides) == 8
    with pytest.raises(ValueError):
        Slide(title="t", bullets=[])




def _refused(message: str) -> litellm.BadRequestError:
    return litellm.BadRequestError(message, model="m", llm_provider="openai")


class _Reply(BaseModel):
    answer: str


def test_a_reply_the_provider_refused_for_its_schema_is_sampled_again(monkeypatch):
    good = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer": "ok"}'))], usage=None)
    replies = [_refused("Generated JSON does not match the expected schema."), good]

    def fake(**_):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(litellm, "completion", fake)

    assert gateway.complete_structured("hello", _Reply).answer == "ok"


def test_any_other_bad_request_is_not_sampled_again(monkeypatch):
    calls = []

    def fake(**_):
        calls.append(1)
        raise _refused("model not found")

    monkeypatch.setattr(litellm, "completion", fake)
    with pytest.raises(litellm.BadRequestError):
        gateway.complete_structured("hello", _Reply)
    assert len(calls) == 1


def test_a_request_does_not_wait_on_a_rate_limit(monkeypatch, no_waiting):
    calls = []

    def fake(**_):
        calls.append(1)
        raise _rate_limit()

    monkeypatch.setattr(gateway.litellm, "completion", fake)
    token = gateway._waits_on_rate_limit.set(False)
    try:
        with pytest.raises(litellm.RateLimitError):
            gateway.complete("hi")
    finally:
        gateway._waits_on_rate_limit.reset(token)
    assert len(calls) == 1 and no_waiting == []


def test_a_background_job_waits_on_a_rate_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(queue, "_beat_until", lambda *_: None)
    monkeypatch.setattr(queue, "execute_job_step", lambda _: seen.append(gateway._waits_on_rate_limit.get()))
    token = gateway._waits_on_rate_limit.set(False)
    try:
        queue.run_claimed_job(SimpleNamespace(id="job"), "worker")
    finally:
        gateway._waits_on_rate_limit.reset(token)
    assert seen == [True]
