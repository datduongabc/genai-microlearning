"""Cross-encoder reranking (mục 2 kiến trúc): chấm lại các chunk ứng viên theo câu hỏi.

Jev (TypeSafe's API) answers, for each candidate, the probability that the passage holds what the
query needs. A self-hosted Jina reranker served this before; Jev ranked better and stayed fast under
load (docs/measurements/jev-rerank/, 2026-10-14-crosslingual-rerank.md), and Jina was removed once
sending passages out was accepted. When Jev cannot answer, the search order stands in."""

import concurrent.futures
import logging
import random
import time

import httpx

from app import timing
from app.config import settings
from app.models import Chunk

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 2.0

# One call per candidate ranked better than one call for all (docs/measurements/jev-rerank/).
JEV_QUESTION = "Does the passage contain the information needed to answer the query?"
_JEV_RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
# A longer wait would hold one of the shared rerank threads; past the retries the search order
# stands in for the scores, which costs a little ranking quality and no generation.
MAX_RETRY_AFTER_SECONDS = 5.0
# Shared by every rerank in this process, so the limit holds however many run at once.
_jev_pool = concurrent.futures.ThreadPoolExecutor(settings.jev_max_concurrent, thread_name_prefix="jev")
_jev_client = httpx.Client(timeout=TIMEOUT_SECONDS)


# The key or the credit: these last until someone acts, unlike a busy or failing service.
_JEV_NEEDS_ATTENTION = {401, 402, 403}


def _jev_delay(retry_after: str | None, attempt: int) -> float:
    try:
        # Seconds only: an HTTP date, NaN or a negative number falls through to the backoff.
        delay = float(retry_after)
        if not 0 <= delay < float("inf"):
            raise ValueError(retry_after)
    except (TypeError, ValueError):
        delay = BASE_BACKOFF_SECONDS * 2 ** (attempt - 1) * (0.5 + random.random())
    return min(delay, MAX_RETRY_AFTER_SECONDS)


def _ask_jev(query: str, text: str, attempts: int = MAX_ATTEMPTS) -> tuple[float, int]:
    """The probability that this passage answers the query, and the retries it took."""
    for attempt in range(1, attempts + 1):
        response = _jev_client.post(
            settings.jev_url,
            headers={"Authorization": f"Bearer {settings.typesafe_api_key}"},
            json={"model": settings.jev_model, "state": {"query": query, "passage": text},
                  "questions": {"relevant": {"type": "noul", "instructions": JEV_QUESTION}}},
        )
        if response.status_code not in _JEV_RETRY_STATUSES or attempt == attempts:
            response.raise_for_status()
            return response.json()["answers"]["relevant"]["noul"], attempt - 1
        time.sleep(_jev_delay(response.headers.get("retry-after"), attempt))
    raise AssertionError("unreachable")


_no_key_reported = False


def _search_order(count: int) -> list[float]:
    """Decreasing as RRF does, so Adaptive-k has no large gap to cut at."""
    return [1 / (60 + position) for position in range(count)]


def _jev_scores(query: str, chunks: list[Chunk], span: dict, attempts: int = MAX_ATTEMPTS) -> list[float]:
    """A failed call must not send a generation back to the queue, so the search order stands in for the scores."""
    if not settings.typesafe_api_key:
        # No key (tests, local runs without Jev): nothing is sent, and the search order stands. Said once
        # per process in the words the fallback alarm counts, so a deployment missing the key is noticed.
        global _no_key_reported
        if not _no_key_reported:
            _no_key_reported = True
            logger.warning("jev_fallback status=None, keeping the search order: no TYPESAFE_API_KEY")
        span["fallback"] = True
        return _search_order(len(chunks))
    try:
        answers = list(_jev_pool.map(lambda c: _ask_jev(query, c.content, attempts), chunks))
    except (httpx.HTTPError, KeyError, ValueError) as error:
        # Retrieval quality has dropped with nothing else to show it. The fixed text is for a log-based alert.
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        logger.log(logging.ERROR if status in _JEV_NEEDS_ATTENTION else logging.WARNING,
                   "jev_fallback status=%s, keeping the search order: %s", status, error)
        span["fallback"] = True
        return _search_order(len(chunks))
    span["retries"] = sum(retries for _, retries in answers)
    return [score for score, _ in answers]


def rerank_scored(query: str, chunks: list[Chunk], attempts: int = MAX_ATTEMPTS,
                  span: dict | None = None) -> list[tuple[Chunk, float]]:
    """Every chunk with its score, best first. Not cut to top_k here: Adaptive-k needs the whole
    score distribution to find its own cut (see search.py). `span`, when given, receives what the
    rerank recorded, such as `fallback` when the scores are only the search order."""
    if not chunks:
        return []
    # Includes any wait for a slot or a retry: that is what the generation experiences under load.
    with timing.timed("rerank", candidates=len(chunks)) as fields:
        scores = _jev_scores(query, chunks, fields, attempts)
    if span is not None:
        span.update(fields)
    # A stable sort keeps the search order among equal scores, which matters for Jev's two-decimal probabilities.
    return sorted(zip(chunks, scores, strict=True), key=lambda pair: pair[1], reverse=True)


def rerank(query: str, chunks: list[Chunk], top_k: int = 8) -> list[Chunk]:
    return [chunk for chunk, _score in rerank_scored(query, chunks)[:top_k]]
