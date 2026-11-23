"""A chat question must not wait out the provider's per-minute limit: it falls back to keyword search."""

import litellm
import pytest
from litellm.exceptions import RateLimitError

from app.ingestion import embeddings
from app.retrieval import search


def test_a_refused_chat_query_falls_back_to_keywords_without_sleeping(monkeypatch):
    calls = []

    def refuse(**kwargs):
        calls.append(kwargs)
        raise RateLimitError("limit", llm_provider="openai", model="embed-default")

    monkeypatch.setattr(litellm, "embedding", refuse)
    monkeypatch.setattr(embeddings.time, "sleep", lambda _: pytest.fail("a chat query must not wait"))
    monkeypatch.setattr(search, "sparse_search", lambda db, course_id, query, top_k: ["keyword hit"])

    assert search.search_for_chat(None, "course", "tiến trình là gì") == ["keyword hit"]
    assert len(calls) == 1 and calls[0]["timeout"] == embeddings.CHAT_TIMEOUT_SECONDS


def test_a_job_still_waits_out_the_limit(monkeypatch):
    answers = iter([RateLimitError("limit", llm_provider="openai", model="embed-default")])

    def once_refused(**kwargs):
        error = next(answers, None)
        if error:
            raise error
        return {"data": [{"index": 0, "embedding": [1.0] + [0.0] * (embeddings.EMBEDDING_DIM - 1)}]}

    waits = []
    monkeypatch.setattr(litellm, "embedding", once_refused)
    monkeypatch.setattr(embeddings.time, "sleep", waits.append)

    assert len(embeddings.embed_texts(["một đoạn"])[0]) == embeddings.EMBEDDING_DIM
    assert len(waits) == 1


def test_a_refused_model_call_in_a_request_is_a_503_not_a_500():
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()

    @app.get("/boom")
    def boom():
        raise RateLimitError("limit", llm_provider="openai", model="embed-default")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 503 and "thử lại" in response.json()["detail"]
