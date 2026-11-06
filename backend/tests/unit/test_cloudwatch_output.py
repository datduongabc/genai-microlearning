"""What CloudWatch reads: JSON log lines for Logs Insights and metric filters, and embedded metric
format lines for the queue depth the worker service scales on."""

import json
import logging

from app.jobs.metrics import NAMESPACE, emf_line
from app.logging_config import JsonFormatter


def _record(message, *args, **extra):
    record = logging.LogRecord("app.retrieval.reranker", logging.WARNING, __file__, 1, message, args, None)
    record.__dict__.update(extra)
    return record


def test_a_json_log_line_keeps_the_message_text_and_the_extra_fields():
    line = JsonFormatter().format(_record("jev_fallback status=%s, keeping the search order", 402, course_id="c1"))

    entry = json.loads(line)
    # A metric filter on the fixed opening must match on both log formats.
    assert entry["message"].startswith("jev_fallback status=402")
    assert entry["level"] == "WARNING"
    assert entry["logger"] == "app.retrieval.reranker"
    assert entry["course_id"] == "c1"
    assert "\n" not in line, "one event per line, or CloudWatch splits it"


def test_a_json_log_line_carries_the_traceback_inside_the_line():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = _record("job failed")
        record.exc_info = sys.exc_info()
    entry = json.loads(JsonFormatter().format(record))
    assert "RuntimeError: boom" in entry["exception"]


def test_a_field_json_cannot_encode_does_not_lose_the_line():
    import uuid

    entry = json.loads(JsonFormatter().format(_record("x", job_id=uuid.UUID(int=1))))
    assert entry["job_id"] == "00000000-0000-0000-0000-000000000001"


def test_the_emf_line_declares_every_value_as_a_metric():
    line = emf_line({"JobsReady": 3.0, "JobsRunning": 16.0, "OldestReadyJobAgeSeconds": 42.5}, timestamp_ms=1)

    entry = json.loads(line)
    declared = entry["_aws"]["CloudWatchMetrics"][0]
    assert declared["Namespace"] == NAMESPACE
    assert declared["Dimensions"] == [[]]
    assert {m["Name"]: m["Unit"] for m in declared["Metrics"]} == {
        "JobsReady": "Count", "JobsRunning": "Count", "OldestReadyJobAgeSeconds": "Seconds",
    }
    assert (entry["JobsReady"], entry["JobsRunning"], entry["OldestReadyJobAgeSeconds"]) == (3.0, 16.0, 42.5)
    assert entry["_aws"]["Timestamp"] == 1


def test_an_empty_queue_reports_zero_age():
    from app.jobs.metrics import queue_counts

    class EmptyQueue:
        def execute(self, _):
            class Row:
                def one(self):
                    return 0, None
            return Row()

        def scalar(self, _):
            return 0

    assert queue_counts(EmptyQueue()) == {"JobsReady": 0.0, "JobsRunning": 0.0, "OldestReadyJobAgeSeconds": 0.0}
