"""Text to vectors, made by the model behind the gateway's embedding alias (see
devops/gateway/config.yaml): Gemini Embedding through OpenRouter by default, or the optional
self-hosted embedder container. The backend does not carry a model itself, so its image stays light.

A provider limits how many texts it takes per minute (Gemini's free tier: 100, counted per text). A
refused request is waited out and asked again, batch by batch, so one refusal never sends the batches
before it again. When the wait is given up the error goes on to the caller, and the job queue tries
the whole step later."""

import functools
import math
import random
import time

import litellm
from litellm.exceptions import RateLimitError

from app.config import settings
from app.models.models import EMBEDDING_DIM

# The most texts a provider takes in one request; the optional embedder is set to take as many.
BATCH_SIZE = 100
# The limit is per minute, so waiting less would only be refused again.
RATE_LIMIT_WAIT_SECONDS = 60
MAX_RATE_LIMIT_RETRIES = 6
# A job can wait for a slow provider; someone typing a chat question cannot, and chat has the keyword
# search to fall back on.
JOB_TIMEOUT_SECONDS = 120
CHAT_TIMEOUT_SECONDS = 10


def _request(texts: list[str], rate_limit_retries: int = MAX_RATE_LIMIT_RETRIES, timeout: float = JOB_TIMEOUT_SECONDS):
    for attempt in range(rate_limit_retries + 1):
        try:
            return litellm.embedding(
                # "openai/" only means "speak the OpenAI-compatible protocol to api_base"; the gateway
                # decides which provider serves the alias. The size goes in extra_body: the client
                # refuses a `dimensions` argument for a model whose name it does not recognise.
                model=f"openai/{settings.embedding_model}",
                api_base=settings.llm_gateway_url,
                api_key=settings.llm_gateway_key,
                input=texts,
                extra_body={"dimensions": EMBEDDING_DIM},
                timeout=timeout,
            )
        except RateLimitError:
            if attempt == rate_limit_retries:
                raise
            time.sleep(RATE_LIMIT_WAIT_SECONDS + random.uniform(0, 10))


def _unit(vector: list[float]) -> list[float]:
    """A vector the provider cut down to a smaller size is no longer of length 1, and the
    database compares by cosine distance."""
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else list(vector)


def _embed_batch(texts: list[str], **request) -> list[list[float]]:
    items = sorted(_request(texts, **request)["data"], key=lambda item: item["index"])
    vectors = [item["embedding"] for item in items]
    if len(vectors) != len(texts):
        raise ValueError(f"the provider gave {len(vectors)} vectors for {len(texts)} texts")
    for vector in vectors:
        # A ValueError is not retried by the job queue: asking again would give the same size.
        if len(vector) != EMBEDDING_DIM:
            raise ValueError(
                f"the provider gave a vector of {len(vector)} numbers, the database column takes {EMBEDDING_DIM}"
            )
    return [_unit(vector) for vector in vectors]


def embed_texts(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        vectors.extend(_embed_batch(texts[start : start + BATCH_SIZE]))
    return vectors


@functools.lru_cache(maxsize=256)
def _embed_query(text: str, wait: bool) -> tuple[float, ...]:
    request = {} if wait else {"rate_limit_retries": 0, "timeout": CHAT_TIMEOUT_SECONDS}
    return tuple(_embed_batch([text], **request)[0])


def embed_text(text: str, wait: bool = True) -> list[float]:
    """One text, for searching. The same question comes back from a cache: a module asks the same
    thing several times (the quiz and the practice question share their query), and every request
    counts against the provider's limit. wait=False raises at once on a refusal and gives up after
    CHAT_TIMEOUT_SECONDS, for a request someone is waiting on."""
    return list(_embed_query(text, wait))
