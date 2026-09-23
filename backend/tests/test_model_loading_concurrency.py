"""Documents of different courses are processed by parallel worker threads, so
the first use of a model right after start-up can happen on several threads at
once. The model must still be loaded exactly once: each extra copy costs time
and hundreds of MB of memory."""

import threading
import time

import pytest

from app.retrieval import reranker


# The embeddings come from the provider through the gateway, so the reranker is the one model this
# process still loads itself.
@pytest.mark.parametrize(
    ("module", "model_class"),
    [(reranker, "CrossEncoder")],
)
def test_concurrent_first_use_loads_the_model_only_once(monkeypatch, module, model_class):
    constructed: list[object] = []

    class FakeModel:
        def __init__(self, name):
            time.sleep(0.2)  # long enough for every other thread to arrive meanwhile
            constructed.append(self)

    monkeypatch.setattr(module, model_class, FakeModel)
    monkeypatch.setattr(module, "_model", None)

    seen: list[object] = []
    threads = [threading.Thread(target=lambda: seen.append(module._get_model())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(constructed) == 1
    assert len(seen) == 8
    assert all(model is constructed[0] for model in seen)
