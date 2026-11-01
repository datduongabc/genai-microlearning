"""Cross-encoder reranking (mục 2 kiến trúc): chấm lại các chunk ứng viên theo câu hỏi.

The model (Jina v2 multilingual INT8) runs in a container of its own, the reranker service of
devops/docker-compose.yml (Text Embeddings Inference), and is called over HTTP: loading it in the
backend pulled torch into the backend image. Measured to be worth keeping in
docs/measurements/2026-10-14-crosslingual-rerank.md."""

import httpx

from app import timing
from app.config import settings
from app.models import Chunk

# 30 candidates take a few seconds on CPU.
TIMEOUT_SECONDS = 60


def rerank_scored(query: str, chunks: list[Chunk]) -> list[tuple[Chunk, float]]:
    """Every chunk with its score, best first. Not cut to top_k here: Adaptive-k needs the whole
    score distribution to find its own cut (see search.py)."""
    if not chunks:
        return []
    with timing.timed("rerank", candidates=len(chunks)):
        response = httpx.post(
            f"{settings.reranker_url}/rerank",
            # Sigmoid scores for Adaptive-k; a model change can still change their distribution.
            # truncate: cut pairs at the model's context limit rather than refuse the request.
            json={"query": query, "texts": [c.content for c in chunks], "raw_scores": False, "truncate": True},
            timeout=TIMEOUT_SECONDS,
        )
    response.raise_for_status()
    scores = {item["index"]: item["score"] for item in response.json()}
    if len(scores) != len(chunks):
        raise ValueError(f"the reranker scored {len(scores)} of {len(chunks)} chunks")
    return sorted(((c, scores[i]) for i, c in enumerate(chunks)), key=lambda pair: pair[1], reverse=True)


def rerank(query: str, chunks: list[Chunk], top_k: int = 8) -> list[Chunk]:
    return [chunk for chunk, _score in rerank_scored(query, chunks)[:top_k]]
