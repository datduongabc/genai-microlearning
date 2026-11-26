"""Unit tests for worker lifecycle, ECS scale-in protection, CloudWatch EMF metrics, and judge guard."""

import json
import signal
import threading
from types import SimpleNamespace

import httpx
import litellm
import pytest

from app.config import settings
from app.jobs import ecs_protection, metrics, worker
from app.llm import judge

# --- ScaleInProtection & worker -------------------------------------------------------------------


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def put(self, url, json=None):
        self.calls.append((url, json))
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _ok_response():
    req = httpx.Request("PUT", "http://ecs-agent/task-protection/v1/state")
    return httpx.Response(200, json={"protection": {}}, request=req)


def _err_response(status=500):
    req = httpx.Request("PUT", "http://ecs-agent/task-protection/v1/state")
    return httpx.Response(status, json={"error": "boom"}, request=req)


def test_ecs_protection_tracks_busy_transitions_and_retries_after_http_errors(caplog):
    busy = {"flag": False}
    client = _FakeClient([_ok_response(), _ok_response(), _ok_response(), _err_response(503), _ok_response()])
    prot = ecs_protection.ScaleInProtection("http://ecs-agent", lambda: busy["flag"], client=client)

    with caplog.at_level("INFO"):
        # First idle tick transitions None -> False (sends False), second idle tick is skipped.
        prot.tick()
        prot.tick()
        assert prot.protected is False
        assert client.calls == [("http://ecs-agent/task-protection/v1/state", {"ProtectionEnabled": False})]

        # Busy ticks renew PUT every tick, but log enabled=True only on the transition.
        busy["flag"] = True
        prot.tick()
        prot.tick()
        assert prot.protected is True
        assert client.calls[-1][1] == {
            "ProtectionEnabled": True,
            "ExpiresInMinutes": ecs_protection.PROTECTION_MINUTES,
        }
        assert len([r for r in caplog.records if "scale_in_protection enabled=True" in r.message]) == 1

        # Transitioning back to idle hits a 503 -> protected stays True so the next tick retries.
        busy["flag"] = False
        prot.tick()
        assert prot.protected is True
        prot.tick()
        assert prot.protected is False
        assert len(client.calls) == 5


def test_ecs_protection_start_noops_without_agent_uri_and_loops_with_agent_uri(monkeypatch):
    monkeypatch.delenv("ECS_AGENT_URI", raising=False)
    assert ecs_protection.start(threading.Event(), lambda: False) is None

    monkeypatch.setenv("ECS_AGENT_URI", "http://ecs-local")
    monkeypatch.setattr(ecs_protection, "CHECK_INTERVAL_SECONDS", 0.01)
    ticks = threading.Event()

    class StubProtection:
        def __init__(self, uri, busy):
            self.uri = uri
            self.busy = busy

        def tick(self):
            ticks.set()

    monkeypatch.setattr(ecs_protection, "ScaleInProtection", StubProtection)
    stop = threading.Event()
    thread = ecs_protection.start(stop, lambda: True)
    assert thread is not None and thread.daemon is True
    assert ticks.wait(timeout=2)
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_worker_run_and_main_wire_signals_start_stop_and_protection_join(monkeypatch):
    events = []
    joined = []

    class FakeProtectionThread:
        def join(self, timeout=None):
            joined.append(timeout)

    monkeypatch.setattr(worker, "start_worker", lambda: events.append("start_worker"))
    monkeypatch.setattr(worker, "stop_worker", lambda: events.append("stop_worker"))
    monkeypatch.setattr(worker.ecs_protection, "start", lambda stop, busy: FakeProtectionThread())

    stop = threading.Event()
    stop.set()
    worker.run(stop)
    assert events == ["start_worker", "stop_worker"]
    assert joined == [5]

    # When ecs_protection returns None, run completes without joining.
    events.clear()
    monkeypatch.setattr(worker.ecs_protection, "start", lambda stop, busy: None)
    worker.run(stop)
    assert events == ["start_worker", "stop_worker"]

    # main() configures logging, hooks SIGTERM/SIGINT to stop.set(), and calls run(stop).
    registered = {}
    monkeypatch.setattr(worker, "configure_app_logging", lambda: events.append("logging"))
    monkeypatch.setattr(worker.signal, "signal", lambda sig, handler: registered.__setitem__(sig, handler))
    monkeypatch.setattr(worker, "run", lambda s: events.append(s))

    worker.main()
    assert set(registered) == {signal.SIGTERM, signal.SIGINT}
    stop_event = events[-1]
    assert not stop_event.is_set()
    registered[signal.SIGTERM](signal.SIGTERM, None)
    assert stop_event.is_set()


# --- CloudWatch EMF metrics -----------------------------------------------------------------------


def test_emf_line_sets_units_dimensions_and_timestamp():
    raw = metrics.emf_line(
        {"JobsReady": 2.0, "RenderJobsReady": 1.0, "OldestReadyJobAgeSeconds": 14.5},
        timestamp_ms=1_700_000_000_000,
    )
    payload = json.loads(raw)
    aws = payload["_aws"]
    assert aws["Timestamp"] == 1_700_000_000_000
    metric_spec = aws["CloudWatchMetrics"][0]
    assert metric_spec["Namespace"] == metrics.NAMESPACE
    assert metric_spec["Dimensions"] == [[]]
    assert metric_spec["Metrics"] == [
        {"Name": "JobsReady", "Unit": "Count"},
        {"Name": "RenderJobsReady", "Unit": "Count"},
        {"Name": "OldestReadyJobAgeSeconds", "Unit": "Seconds"},
    ]
    assert payload["OldestReadyJobAgeSeconds"] == 14.5


def test_report_queue_metrics_skips_when_disabled_and_writes_stdout_when_enabled(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(settings, "queue_metrics", False)
    monkeypatch.setattr(metrics, "WorkerSessionLocal", lambda: called.append("opened"))
    metrics.report_queue_metrics()
    assert called == []
    assert capsys.readouterr().out == ""

    closed = []

    class FakeSession:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(settings, "queue_metrics", True)
    monkeypatch.setattr(metrics, "WorkerSessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        metrics,
        "queue_counts",
        lambda db: {"JobsReady": 3.0, "RenderJobsReady": 0.0, "JobsRunning": 1.0, "OldestReadyJobAgeSeconds": 8.0},
    )
    metrics.report_queue_metrics()
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert closed == [True]
    assert parsed["JobsReady"] == 3.0 and parsed["JobsRunning"] == 1.0


# --- Cross-family judge guard ---------------------------------------------------------------------


MODEL_INFO = [
    {"model_name": "claude-fast", "litellm_params": {"model": "openrouter/anthropic/claude-haiku-4.5"},
     "model_info": {"max_input_tokens": 200_000}},
    {"model_name": "openai-fast", "litellm_params": {"model": "openai/gpt-5.4-mini"},
     "model_info": {"max_input_tokens": 128_000}},
    {"model_name": "gemini-flash", "litellm_params": {"model": "openrouter/google/gemini-3.8-flash"},
     "model_info": None},
    {"model_name": "deepseek-flash", "litellm_params": {"model": "openrouter/deepseek/deepseek-v3.2"}},
    {"model_name": "llama-groq", "litellm_params": {"model": "groq/meta-llama/llama-4-scout"}},
]


@pytest.mark.parametrize(
    "litellm_name,expected",
    [
        ("openrouter/google/gemini-3.8-flash", "google"),
        ("groq/meta-llama/custom-model", "meta-llama"),
        ("groq/moonshotai/kimi-k2", "moonshotai"),
        ("openai/gpt-5.4-mini", "openai"),
        ("custom-provider/some-model", "custom-provider"),
        ("standalone", "standalone"),
    ],
)
def test_model_family_resolves_vendor_prefixes_and_groq_middle_segment(litellm_name, expected):
    assert judge.model_family(litellm_name) == expected


def test_refuse_same_family_and_eligible_judges_enforce_provenance_guards():
    # Unknown judge alias -> explicit error listing gateway aliases
    with pytest.raises(ValueError, match="Gateway không có alias chấm 'missing'"):
        judge.refuse_same_family("missing", [], MODEL_INFO)

    # Unknown generator alias with empty generation_models -> refused
    with pytest.raises(ValueError, match="gateway không có alias 'ghost-gen'"):
        judge.refuse_same_family("openai-fast", [("G1", "ghost-gen", [])], MODEL_INFO)

    # Same family via generator alias -> refused
    with pytest.raises(ValueError, match="cùng hãng openai"):
        judge.refuse_same_family("openai-fast", [("G1", "openai-fast", [])], MODEL_INFO)

    # Same family detected via actual served_model provenance -> refused
    with pytest.raises(ValueError, match="cùng hãng với model thực sự"):
        judge.refuse_same_family(
            "gemini-flash",
            [("G1", "deepseek-flash", [{"served_model": "vertex/gemma-3-27b"}])],
            MODEL_INFO,
        )

    # Cross-family succeeds and returns resolved LiteLLM model name
    resolved = judge.refuse_same_family(
        "claude-fast",
        [("G1", "openai-fast", [{"served_model": "openai/gpt-5.4-mini"}])],
        MODEL_INFO,
    )
    assert resolved == "openrouter/anthropic/claude-haiku-4.5"

    # eligible_judges drops missing pool aliases + generators' families, handling model_info=None cleanly
    subset_info = [m for m in MODEL_INFO if m["model_name"] != "deepseek-flash"]
    elig = judge.eligible_judges(
        [
            ("G1", "openai-fast", []),
            ("G2", "deepseek-flash", [{"served_model": "deepseek/deepseek-chat"}]),
        ],
        subset_info,
    )
    assert [c["alias"] for c in elig] == ["claude-fast", "gemini-flash"]
    assert elig[0]["max_input_tokens"] == 200_000 and elig[1]["max_input_tokens"] is None


def test_is_quota_error_detects_rate_limit_and_http_429():
    rate_err = litellm.RateLimitError(message="quota", llm_provider="openai", model="gpt")
    rate_err.status_code = 503
    assert judge.is_quota_error(rate_err) is True
    assert judge.is_quota_error(SimpleNamespace(status_code=429)) is True
    assert judge.is_quota_error(SimpleNamespace(status_code=500)) is False
    assert judge.is_quota_error(RuntimeError("network blip")) is False
