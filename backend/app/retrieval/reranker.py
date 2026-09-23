import threading

from sentence_transformers import CrossEncoder

from app.models import Chunk

# mục 2 kien truc: Cross-Encoder Reranking (BGE-reranker cuc bo, khong dung
# Cohere API) -- loc candidate xuong top 5-8 chunk hyper-relevant.
_MODEL_NAME = "BAAI/bge-reranker-base"
_model: CrossEncoder | None = None
# Same reason as embeddings._model_lock: load the model once, not once per thread.
_model_lock = threading.Lock()


def _get_model() -> CrossEncoder:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = CrossEncoder(_MODEL_NAME)
    return _model


def rerank_scored(query: str, chunks: list[Chunk]) -> list[tuple[Chunk, float]]:
    """Tra ve TOAN BO chunk da cham diem, sap xep giam dan -- khong cat
    top_k o day (Adaptive-k can toan bo phan bo diem de tu tim nguong cat,
    xem retrieval.py)."""
    if not chunks:
        return []
    pairs = [(query, c.content) for c in chunks]
    scores = _get_model().predict(pairs)
    return sorted(zip(chunks, scores, strict=True), key=lambda pair: pair[1], reverse=True)


def rerank(query: str, chunks: list[Chunk], top_k: int = 8) -> list[Chunk]:
    return [chunk for chunk, _score in rerank_scored(query, chunks)[:top_k]]
