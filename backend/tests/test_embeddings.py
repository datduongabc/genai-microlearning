"""Text goes to the embedding provider through the gateway and comes back as unit-length vectors of
the size the database column has. The provider is replaced by a recorder: a test sees what was asked
of it and decides what it answers."""

import math

import pytest
from litellm.exceptions import AuthenticationError, RateLimitError

from app.config import settings
from app.ingestion import embeddings
from app.jobs.queue import is_terminal_error
from app.models.models import EMBEDDING_DIM


class Provider:
    def __init__(self):
        self.calls: list[dict] = []
        self.dim = EMBEDDING_DIM
        # One entry per call, in order: an exception to raise, or None for a call that goes through.
        self.failures: list[Exception | None] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        # The first component is the length of the text, so a vector says which text it is for.
        # Not unit length on purpose: a provider that cuts a vector down does not keep it that way.
        data = [
            {"index": i, "embedding": [float(len(text)), 2.0] + [0.0] * (self.dim - 2)}
            for i, text in enumerate(kwargs["input"])
        ]
        return {"data": list(reversed(data))}  # not in the order asked, on purpose


@pytest.fixture
def provider(monkeypatch):
    p = Provider()
    monkeypatch.setattr(embeddings.litellm, "embedding", p)
    return p


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(embeddings.time, "sleep", waited.append)
    return waited


def _rate_limit():
    return RateLimitError(message="quota", llm_provider="openai", model="embed-default")


def _norm(vector):
    return math.sqrt(sum(x * x for x in vector))


def test_vectors_have_the_size_of_the_database_column_and_unit_length(provider):
    vectors = embeddings.embed_texts(["một", "hai hai", "ba ba ba"])

    assert len(vectors) == 3
    assert all(len(v) == EMBEDDING_DIM for v in vectors)
    assert all(abs(_norm(v) - 1.0) < 1e-9 for v in vectors)


def test_each_vector_is_for_the_text_at_its_place_whatever_order_the_provider_answers_in(provider):
    texts = ["a" * 3, "b" * 7, "c" * 5]

    vectors = embeddings.embed_texts(texts)

    for text, vector in zip(texts, vectors, strict=True):
        assert abs(vector[0] / vector[1] - len(text) / 2) < 1e-9


def test_nothing_to_embed_asks_nothing(provider):
    assert embeddings.embed_texts([]) == []
    assert provider.calls == []


def test_a_long_list_is_sent_in_batches_the_provider_accepts_and_comes_back_in_order(provider):
    texts = ["x" * n for n in range(1, 251)]

    vectors = embeddings.embed_texts(texts)

    assert [len(call["input"]) for call in provider.calls] == [100, 100, 50]
    assert len(vectors) == 250
    for text, vector in zip(texts, vectors, strict=True):
        assert abs(vector[0] / vector[1] - len(text) / 2) < 1e-9


def test_the_request_goes_to_the_gateway_for_the_size_of_the_column(provider):
    embeddings.embed_texts(["một"])

    call = provider.calls[0]
    assert call["model"].endswith("/" + settings.embedding_model)
    assert call["api_base"] == settings.llm_gateway_url
    assert call["extra_body"]["dimensions"] == EMBEDDING_DIM


def test_a_refused_request_is_waited_out_and_tried_again_without_losing_the_batch(provider, sleeps):
    provider.failures = [_rate_limit(), _rate_limit()]

    vectors = embeddings.embed_texts(["một", "hai"])

    assert len(vectors) == 2
    assert len(provider.calls) == 3
    assert len(sleeps) == 2
    assert all(wait >= 30 for wait in sleeps)  # the limit is per minute: a short wait would only fail again


def test_the_wait_is_given_up_after_a_while_so_the_job_can_be_retried_later(provider, sleeps):
    provider.failures = [_rate_limit() for _ in range(embeddings.MAX_RATE_LIMIT_RETRIES + 1)]

    with pytest.raises(RateLimitError):
        embeddings.embed_texts(["một"])

    assert len(sleeps) == embeddings.MAX_RATE_LIMIT_RETRIES


def test_a_batch_that_was_refused_does_not_send_the_batches_before_it_again(provider, sleeps):
    texts = ["x" * n for n in range(1, 151)]
    provider.failures = [None, _rate_limit()]  # the first batch goes through, the second is refused once

    vectors = embeddings.embed_texts(texts)

    assert len(vectors) == 150
    assert [len(call["input"]) for call in provider.calls] == [100, 50, 50]


def test_an_error_that_waiting_cannot_fix_is_raised_at_once(provider, sleeps):
    provider.failures = [AuthenticationError(message="bad key", llm_provider="openai", model="embed-default")]

    with pytest.raises(AuthenticationError):
        embeddings.embed_texts(["một"])

    assert sleeps == []
    assert len(provider.calls) == 1


def test_a_vector_of_the_wrong_size_is_refused_and_is_not_worth_a_retry(provider):
    provider.dim = 3072  # the provider's own default, if the size was not asked for

    with pytest.raises(ValueError) as error:
        embeddings.embed_texts(["một"])

    assert str(EMBEDDING_DIM) in str(error.value)
    assert is_terminal_error(error.value)


def test_one_text_is_one_vector(provider):
    vector = embeddings.embed_text("hai hai")

    assert len(vector) == EMBEDDING_DIM
    assert abs(_norm(vector) - 1.0) < 1e-9


def test_the_same_question_is_not_asked_of_the_provider_twice(provider):
    first = embeddings.embed_text("Ai là nhà tư tưởng?")
    second = embeddings.embed_text("Ai là nhà tư tưởng?")
    embeddings.embed_text("Một câu hỏi khác")

    assert first == second
    assert len(provider.calls) == 2


def test_changing_a_returned_vector_does_not_change_what_the_next_caller_gets(provider):
    vector = embeddings.embed_text("hai hai")
    original = list(vector)
    vector[0] = 99.0

    assert embeddings.embed_text("hai hai") == original
