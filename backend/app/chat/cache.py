"""Answers to a first question reused for the same question on the same documents. A class asks the same
things ("Deadlock là gì?") again and again; the second asker gets the saved answer at once, without a search
or a model call.

The key holds everything an answer depends on: the course, the exact set of documents the asker may read
(a learner and a lecturer see different sets, and a document added, removed or opened changes it), the
model, and the question with its spacing and case evened out. A follow-up question is never cached: its
meaning depends on the conversation before it.

Valkey holds the answers when the API runs as several tasks (AWS), so every task shares them; locally, a
small dictionary in the process does. A cache that fails is a miss, never a failed answer."""

import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict

from app.config import settings

logger = logging.getLogger(__name__)

_VERSION = "chat-answer:v1"
_LOCAL_LIMIT = 2000
_local: OrderedDict[str, tuple[float, str]] = OrderedDict()
_local_lock = threading.Lock()
_client = None


def _redis():
    global _client
    uri = settings.rate_limit_storage_uri
    if not uri.startswith(("redis://", "rediss://")):
        return None
    if _client is None:
        import redis

        # Short timeouts: a slow cache must not delay the first word more than a miss would.
        _client = redis.Redis.from_url(uri, socket_timeout=0.3, socket_connect_timeout=0.3)
    return _client


def cache_key(course_id, documents_version: str, model: str, question: str) -> str:
    normal = re.sub(r"\s+", " ", question.casefold()).strip().rstrip("?!.。 ")
    raw = json.dumps([str(course_id), documents_version, model, normal], ensure_ascii=False)
    return f"{_VERSION}:{hashlib.sha256(raw.encode()).hexdigest()}"


def get(key: str) -> dict | None:
    if settings.chat_cache_seconds <= 0:
        return None
    try:
        client = _redis()
        if client is not None:
            value = client.get(key)
        else:
            with _local_lock:
                expires, value = _local.get(key, (0.0, None))
                if expires < time.monotonic():
                    _local.pop(key, None)
                    value = None
        return json.loads(value) if value else None
    except Exception as error:  # a cache that fails is a miss
        logger.warning("chat cache read failed: %s: %s", type(error).__name__, error)
        return None


def put(key: str, answer: dict) -> None:
    if settings.chat_cache_seconds <= 0:
        return
    value = json.dumps(answer, ensure_ascii=False)
    try:
        client = _redis()
        if client is not None:
            client.set(key, value, ex=settings.chat_cache_seconds)
            return
        with _local_lock:
            _local[key] = (time.monotonic() + settings.chat_cache_seconds, value)
            _local.move_to_end(key)
            while len(_local) > _LOCAL_LIMIT:
                _local.popitem(last=False)
    except Exception as error:
        logger.warning("chat cache write failed: %s: %s", type(error).__name__, error)


def clear_local() -> None:
    with _local_lock:
        _local.clear()
