"""The API and the job worker can run as separate processes, and share their rate limit counters."""

import threading
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.auth.rate_limit import build_limiter
from app.jobs import worker


def test_api_starts_the_worker_only_when_configured():
    for configured, calls in ((True, 1), (False, 0)):
        with patch.object(main.settings, "run_job_worker", configured), \
                patch.object(main, "start_worker") as start, patch.object(main, "stop_worker") as stop, \
                TestClient(main.create_app()):
            pass
        assert (start.call_count, stop.call_count) == (calls, calls)


def test_limiter_uses_the_configured_store_and_survives_its_outage():
    limiter = build_limiter("redis://redis:6379/0")
    assert limiter._storage_uri == "redis://redis:6379/0"
    assert limiter._in_memory_fallback_enabled


def test_worker_process_runs_until_told_to_stop():
    stop = threading.Event()
    stop.set()
    with patch.object(worker, "start_worker") as start, patch.object(worker, "stop_worker") as end:
        worker.run(stop)
    start.assert_called_once()
    end.assert_called_once()
