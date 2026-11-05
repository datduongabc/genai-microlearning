"""Cross-encoder reranking (mục 2 kiến trúc): chấm lại các chunk ứng viên theo câu hỏi.

The model (Jina v2 multilingual INT8) runs in a container of its own, the reranker service of
devops/docker-compose.yml (Text Embeddings Inference), and is called over HTTP: loading it in the
backend pulled torch into the backend image. Measured to be worth keeping in
docs/measurements/2026-10-14-crosslingual-rerank.md."""

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


def _post(query: str, chunks: list[Chunk]) -> httpx.Response:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with _slots:
                response = httpx.post(
                    f"{settings.reranker_url}/rerank",
                    # Sigmoid scores for Adaptive-k; a model change can still change their distribution.
                    # truncate: cut pairs at the model's context limit rather than refuse the request.
                    json={"query": query, "texts": [c.content for c in chunks], "raw_scores": False, "truncate": True},
                    timeout=TIMEOUT_SECONDS,
                )
            if response.status_code not in _RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                response.raise_for_status()
                return response
            reason = str(response.status_code)
        except httpx.TransportError as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            reason = type(exc).__name__
        delay = BASE_BACKOFF_SECONDS * 2 ** (attempt - 1) * (0.5 + random.random())
        logger.warning("reranker busy (%s), retry %d in %.1fs", reason, attempt, delay)
        time.sleep(delay)
    raise AssertionError("unreachable")


def rerank_scored(query: str, chunks: list[Chunk]) -> list[tuple[Chunk, float]]:
    """Every chunk with its score, best first. Not cut to top_k here: Adaptive-k needs the whole
    score distribution to find its own cut (see search.py)."""
    if not chunks:
        return []
    # Includes any wait for a slot or a retry: that is what the generation experiences under load.
    with timing.timed("rerank", candidates=len(chunks)):
        response = _post(query, chunks)
    scores = {item["index"]: item["score"] for item in response.json()}
    if len(scores) != len(chunks):
        raise ValueError(f"the reranker scored {len(scores)} of {len(chunks)} chunks")
    return sorted(((c, scores[i]) for i, c in enumerate(chunks)), key=lambda pair: pair[1], reverse=True)


def rerank(query: str, chunks: list[Chunk], top_k: int = 8) -> list[Chunk]:
    return [chunk for chunk, _score in rerank_scored(query, chunks)[:top_k]]
