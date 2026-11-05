"""Cross-encoder reranking (mục 2 kiến trúc): chấm lại các chunk ứng viên theo câu hỏi.

The model (Jina v2 multilingual INT8) runs in a container of its own, the reranker service of
devops/docker-compose.yml (Text Embeddings Inference), and is called over HTTP: loading it in the
backend pulled torch into the backend image. Measured to be worth keeping in
docs/measurements/2026-10-14-crosslingual-rerank.md."""

import concurrent.futures
import logging
import random
import threading
import time

import httpx

from app import timing
from app.config import settings
from app.models import Chunk

logger = logging.getLogger(__name__)

# 30 candidates take a few seconds on CPU.
TIMEOUT_SECONDS = 60
# Overload is temporary. Failing here would send a whole generation back to the queue and repeat
# every model call it had already paid for.
MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 2.0
_RETRY_STATUSES = {429, 502, 503, 504}
_slots = threading.BoundedSemaphore(settings.reranker_max_concurrent)

# Compared with Jina in docs/measurements/jev-rerank/: one call per candidate ranked better than one call for all.
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_QUESTION = "Does the passage contain the information needed to answer the query?"
_JEV_RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
# Shared by every rerank in this process, so the limit holds however many run at once.
_jev_pool = concurrent.futures.ThreadPoolExecutor(settings.jev_max_concurrent, thread_name_prefix="jev")
_jev_client = httpx.Client(timeout=TIMEOUT_SECONDS)


def _post(query: str, chunks: list[Chunk], attempts: int) -> httpx.Response:
    for attempt in range(1, attempts + 1):
        try:
            with _slots:
                response = httpx.post(
                    f"{settings.reranker_url}/rerank",
                    # Sigmoid scores for Adaptive-k; a model change can still change their distribution.
                    # truncate: cut pairs at the model's context limit rather than refuse the request.
                    json={"query": query, "texts": [c.content for c in chunks], "raw_scores": False, "truncate": True},
                    timeout=TIMEOUT_SECONDS,
                )
            if response.status_code not in _RETRY_STATUSES or attempt == attempts:
                response.raise_for_status()
                return response
            reason = str(response.status_code)
        except httpx.TransportError as exc:
            if attempt == attempts:
                raise
            reason = type(exc).__name__
        delay = BASE_BACKOFF_SECONDS * 2 ** (attempt - 1) * (0.5 + random.random())
        logger.warning("reranker busy (%s), retry %d in %.1fs", reason, attempt, delay)
        time.sleep(delay)
    raise AssertionError("unreachable")


def _jina_scores(query: str, chunks: list[Chunk], attempts: int) -> list[float]:
    response = _post(query, chunks, attempts)
    scores = {item["index"]: item["score"] for item in response.json()}
    if len(scores) != len(chunks):
        raise ValueError(f"the reranker scored {len(scores)} of {len(chunks)} chunks")
    return [scores[i] for i in range(len(chunks))]


def _ask_jev(query: str, text: str) -> tuple[float, int]:
    """The probability that this passage answers the query, and the retries it took."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = _jev_client.post(
            JEV_URL,
            headers={"Authorization": f"Bearer {settings.typesafe_api_key}"},
            json={"model": settings.jev_model, "state": {"query": query, "passage": text},
                  "questions": {"relevant": {"type": "noul", "instructions": JEV_QUESTION}}},
        )
        if response.status_code not in _JEV_RETRY_STATUSES or attempt == MAX_ATTEMPTS:
            response.raise_for_status()
            return response.json()["answers"]["relevant"]["noul"], attempt - 1
        retry_after = response.headers.get("retry-after")
        time.sleep(float(retry_after) if retry_after else BASE_BACKOFF_SECONDS * 2 ** (attempt - 1) * (0.5 + random.random()))
    raise AssertionError("unreachable")


def _jev_scores(query: str, chunks: list[Chunk], span: dict) -> list[float]:
    """A failed call must not send a generation back to the queue, so the search order stands in for the scores."""
    try:
        answers = list(_jev_pool.map(lambda c: _ask_jev(query, c.content), chunks))
    except (httpx.HTTPError, KeyError, ValueError) as error:
        logger.warning("Jev failed, keeping the search order: %s", error)
        span["fallback"] = True
        # Decreasing as RRF does, so Adaptive-k has no large gap to cut at.
        return [1 / (60 + position) for position in range(len(chunks))]
    span["retries"] = sum(retries for _, retries in answers)
    return [score for score, _ in answers]


def rerank_scored(query: str, chunks: list[Chunk], attempts: int = MAX_ATTEMPTS) -> list[tuple[Chunk, float]]:
    """Every chunk with its score, best first. Not cut to top_k here: Adaptive-k needs the whole
    score distribution to find its own cut (see search.py)."""
    if not chunks:
        return []
    # Includes any wait for a slot or a retry: that is what the generation experiences under load.
    with timing.timed("rerank", candidates=len(chunks), provider=settings.reranker_provider) as span:
        scores = _jev_scores(query, chunks, span) if settings.reranker_provider == "jev" else _jina_scores(query, chunks, attempts)
    # A stable sort keeps the search order among equal scores, which matters for Jev's two-decimal probabilities.
    return sorted(zip(chunks, scores, strict=True), key=lambda pair: pair[1], reverse=True)


def rerank(query: str, chunks: list[Chunk], top_k: int = 8) -> list[Chunk]:
    return [chunk for chunk, _score in rerank_scored(query, chunks)[:top_k]]
