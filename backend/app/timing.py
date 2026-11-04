"""Time spans of one generation run: one per stage, and one per call made inside a stage (model
call, query embedding, BM25, rerank), so a module can say where its minutes went.

Kept in a ContextVar like the model provenance in app/llm/gateway.py: the worker runs several
generations at once, and a global list would mix their spans. Outside `capture_timings()` every
function here is a no-op, so the request path and the tests pay nothing for it."""

import contextlib
import contextvars
import time

_spans: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar("_spans", default=None)
_stage: contextvars.ContextVar[str | None] = contextvars.ContextVar("_stage", default=None)


@contextlib.contextmanager
def capture_timings():
    spans: list[dict] = []
    token = _spans.set(spans)
    try:
        yield spans
    finally:
        _spans.reset(token)


def record(kind: str, seconds: float, **extra) -> None:
    spans = _spans.get()
    if spans is not None:
        spans.append({"kind": kind, "stage": _stage.get(), "seconds": round(seconds, 4), **extra})


@contextlib.contextmanager
def timed(kind: str, **extra):
    """Record how long the block took. The yielded dict takes fields known only at the end, such as
    the token counts in a reply."""
    fields = dict(extra)
    start = time.perf_counter()
    try:
        yield fields
    finally:
        record(kind, time.perf_counter() - start, **fields)


@contextlib.contextmanager
def stage(name: str):
    """A pipeline stage; calls inside it carry its name. A stage that raises is recorded as failed."""
    token = _stage.set(name)
    start = time.perf_counter()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        _stage.reset(token)
        spans = _spans.get()
        if spans is not None:
            spans.append({"kind": "stage", "stage": name, "seconds": round(time.perf_counter() - start, 4),
                          **({"failed": True} if failed else {})})


def summarize(spans: list[dict]) -> dict[str, dict]:
    """Per stage: its wall time, its model calls with their time and tokens, and the time of each
    other kind of call inside it (`<kind>_seconds`)."""
    summary: dict[str, dict] = {}
    for span in spans:
        name = span["stage"]
        if name is None:
            continue
        entry = summary.setdefault(name, {"seconds": 0.0, "llm_calls": 0, "llm_seconds": 0.0,
                                          "prompt_tokens": 0, "completion_tokens": 0})
        if span["kind"] == "stage":
            entry["seconds"] += span["seconds"]
        elif span["kind"] == "llm":
            entry["llm_calls"] += 1
            entry["llm_seconds"] += span["seconds"]
            entry["prompt_tokens"] += span.get("prompt_tokens") or 0
            entry["completion_tokens"] += span.get("completion_tokens") or 0
        else:
            key = f"{span['kind']}_seconds"
            entry[key] = entry.get(key, 0.0) + span["seconds"]
    return summary
