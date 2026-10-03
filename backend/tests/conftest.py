import pytest

from app.auth.rate_limit import limiter
from app.moodle import client as moodle_client


@pytest.fixture(autouse=True)
def rate_limits(request, monkeypatch):
    """The limits are per client address and every test comes from the same one, so with
    them on, tests that sign up or upload would start failing depending on how many ran
    in the same minute. A test that is about the limits asks for them with the
    `rate_limits` marker and gets a clean counter before and after."""
    if request.node.get_closest_marker("rate_limits"):
        limiter.reset()
        yield
        limiter.reset()
    else:
        monkeypatch.setattr(limiter, "enabled", False)
        yield


def _offline_embedding(**kwargs):
    """Stands in for the embedding provider in every test that does not test the provider itself.

    A hashed bag of words: texts that share words end up close together, which is all a test of
    search needs, and the same text always gives the same vector, so nothing is called over the
    network and no quota is used."""
    import hashlib
    import math
    import re

    from app.models.models import EMBEDDING_DIM

    data = []
    for index, text in enumerate(kwargs["input"]):
        vector = [0.0] * EMBEDDING_DIM
        for word in re.findall(r"\w+", text.lower()):
            slot = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16) % EMBEDDING_DIM
            vector[slot] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        data.append({"index": index, "embedding": [x / norm for x in vector]})
    return {"data": data}


@pytest.fixture(autouse=True)
def offline_embeddings(request, monkeypatch):
    """Embeddings come from the provider through the gateway. A test that is not about them gets
    an offline stand-in, and the query cache is emptied around every test so that a vector
    made by one stand-in is never served to the next. Live tests keep the real provider."""
    from app.ingestion import embeddings

    embeddings._embed_query.cache_clear()
    if not request.node.get_closest_marker("live_llm"):
        monkeypatch.setattr(embeddings.litellm, "embedding", _offline_embedding)
    yield
    embeddings._embed_query.cache_clear()


@pytest.fixture(autouse=True)
def moodle_knows_every_course(monkeypatch):
    """Linking a course first asks Moodle whether it exists. Tests that are not about that
    answer yes for any id instead of needing a Moodle server; the ones that are about it
    replace this."""
    monkeypatch.setattr(
        moodle_client, "get_course", lambda course_id: {"id": course_id, "fullname": "Course", "shortname": "C"}
    )


@pytest.fixture(autouse=True)
def moodle_has_every_lecturer(monkeypatch):
    """Linking a lecturer's Moodle account finds the Moodle user with their email. Tests that are
    not about that give every email exactly one account, 4242; the ones that are replace this."""
    monkeypatch.setattr(
        moodle_client, "find_users_by_email",
        lambda emails: {e.strip().lower(): [4242] for e in emails},
    )


@pytest.fixture(autouse=True)
def moodle_keeps_every_user(monkeypatch):
    """A stored Moodle link is checked against Moodle. Tests that are not about that find every
    linked Moodle user still there; the ones that are replace this."""
    monkeypatch.setattr(moodle_client, "user_exists", lambda user_id: True)


@pytest.fixture(autouse=True)
def moodle_enrols_anyone(monkeypatch):
    """Linking a course and pushing to it enrol the lecturer's Moodle account as its teacher.
    Tests that are not about that let it succeed without a Moodle server; the ones that are
    replace this."""
    monkeypatch.setattr(moodle_client, "enrol_as_teacher", lambda course_id, user_id: None)
