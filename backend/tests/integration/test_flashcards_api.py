"""The lecturer's routes for flashcard sets: generate, edit, publish, delete."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.rate_limit import limiter
from app.auth.security import create_access_token
from app.db import SessionLocal
from app.llm import catalog
from app.main import app
from app.models import (
    Chunk,
    Course,
    Document,
    DocumentStatus,
    FlashcardSet,
    Job,
    JobType,
    MediaStatus,
    User,
)

client = TestClient(app)


def headers(user_id):
    return {"Authorization": "Bearer " + create_access_token(user_id)}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(catalog, "is_available", lambda alias: alias in {"gemini-fast", "claude-pro"})
    monkeypatch.setattr(catalog, "probe", lambda alias: None)
    with SessionLocal() as db:
        def user(name, **extra):
            u = User(email=f"fc-{name}-{uuid.uuid4().hex[:8]}@example.com", name=name, hashed_password="x", **extra)
            db.add(u)
            db.flush()
            return u
        owner, other, learner = user("owner"), user("other"), user("learner", can_author=False)
        course = Course(name="Flashcards", owner_id=owner.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="x.txt", status=DocumentStatus.embedded, raw_text="Heaps.")
        other_doc = Document(course_id=course.id, filename="y.txt", status=DocumentStatus.embedded, raw_text="Graphs.")
        db.add_all([doc, other_doc])
        db.flush()
        chunk = Chunk(document_id=doc.id, course_id=course.id, chunk_index=0, content="A heap is a tree.")
        foreign_chunk = Chunk(document_id=other_doc.id, course_id=course.id, chunk_index=0, content="A graph.")
        db.add_all([chunk, foreign_chunk])
        db.commit()
        ids = {
            "owner": owner.id, "other": other.id, "learner": learner.id, "course": course.id, "doc": doc.id,
            "chunk": chunk.id, "foreign_chunk": foreign_chunk.id,
        }
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids["course"]).delete()
        db.query(User).filter(User.id.in_([ids["owner"], ids["other"], ids["learner"]])).delete()
        db.commit()


def make_set(world, *, status=MediaStatus.draft, cards=None, published=False):
    from datetime import UTC, datetime
    with SessionLocal() as db:
        card_set = FlashcardSet(
            course_id=world["course"], document_id=world["doc"], output_language="en", model_alias="gemini-fast",
            status=status, cards=cards or [], published_at=datetime.now(UTC) if published else None)
        db.add(card_set)
        db.commit()
        return card_set.id


def card(front="Q", back="A", citations=None, **extra):
    return {"front": front, "back": back, "citations": citations or [], **extra}


def test_generating_reserves_one_generation_and_queues_a_job(world):
    response = client.post("/flashcard-sets", params={"document_id": str(world["doc"])},
                           json={"focus": "heaps", "output_language": "en", "model_alias": "gemini-fast"},
                           headers=headers(world["owner"]))

    assert response.status_code == 200
    with SessionLocal() as db:
        card_set = db.get(FlashcardSet, uuid.UUID(response.json()["id"]))
        job = db.get(Job, uuid.UUID(response.json()["job_id"]))
        assert (card_set.status, card_set.focus, card_set.model_alias) == (MediaStatus.generating, "heaps", "gemini-fast")
        assert (job.job_type, job.document_id, job.payload) == (
            JobType.flashcard_generation, world["doc"], {"flashcard_set_id": str(card_set.id)})
        assert db.get(User, world["owner"]).generation_count == 1


def test_a_second_request_while_one_is_running_is_refused_without_a_charge(world):
    url, body = "/flashcard-sets", {"output_language": "vi"}
    params = {"document_id": str(world["doc"])}
    assert client.post(url, params=params, json=body, headers=headers(world["owner"])).status_code == 200
    assert client.post(url, params=params, json=body, headers=headers(world["owner"])).status_code == 409
    with SessionLocal() as db:
        assert db.get(User, world["owner"]).generation_count == 1


@pytest.mark.parametrize("body", [
    {"output_language": "vi", "model_alias": "no-such-model"},
    {"output_language": "vi", "model_alias": "mystery-x"},
    {"output_language": "vi", "focus": "x" * 1001},
    {"output_language": "fr"},
])
def test_an_unusable_request_is_rejected_with_422(world, monkeypatch, body):
    monkeypatch.setattr(catalog, "is_available", lambda alias: alias != "no-such-model")
    response = client.post("/flashcard-sets", params={"document_id": str(world["doc"])}, json=body,
                           headers=headers(world["owner"]))

    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.get(User, world["owner"]).generation_count == 0


def test_a_16th_card_is_rejected_with_422(world):
    set_id = make_set(world)

    response = client.put(f"/flashcard-sets/{set_id}/cards", json={"cards": [card()] * 16},
                          headers=headers(world["owner"]))

    assert response.status_code == 422


def test_an_edited_card_is_marked_as_the_lecturers(world):
    chunk = str(world["chunk"])
    stored = [
        {"front": "Q1", "back": "A1", "citations": [chunk], "author": "model", "flag": None},
        {"front": "Q2", "back": "A2", "citations": [chunk], "author": "model", "flag": "Unsupported"},
        {"front": "Q3", "back": "A3", "citations": [chunk], "author": "model", "flag": "Unsupported"},
    ]
    set_id = make_set(world, cards=stored)

    response = client.put(
        f"/flashcard-sets/{set_id}/cards", headers=headers(world["owner"]),
        json={"cards": [
            card("Q1", "A1", [chunk]),                     # untouched: keeps what it had
            card("Q2", "A2 fixed", [chunk]),               # edited: the lecturer's, flag cleared
            card("Q3", "A3", []),                          # source removed: no source flag
            card("New", "Card", [chunk]),
        ]})

    assert response.status_code == 200
    assert [(c["author"], c["flag"]) for c in response.json()["cards"]] == [
        ("model", None), ("lecturer", None), ("lecturer", "Chưa có nguồn"), ("lecturer", None)]


def test_an_unchanged_flagged_card_keeps_its_flag(world):
    stored = [{"front": "Q", "back": "A", "citations": [], "author": "model", "flag": "Unsupported"}]
    set_id = make_set(world, cards=stored)

    cards = client.put(f"/flashcard-sets/{set_id}/cards", json={"cards": [card("Q", "A")]},
                       headers=headers(world["owner"])).json()["cards"]

    assert (cards[0]["author"], cards[0]["flag"]) == ("model", "Unsupported")


def test_a_citation_from_another_document_is_rejected(world):
    set_id = make_set(world)

    for citation in (str(world["foreign_chunk"]), str(uuid.uuid4()), "not-a-uuid"):
        response = client.put(f"/flashcard-sets/{set_id}/cards", json={"cards": [card(citations=[citation])]},
                              headers=headers(world["owner"]))
        assert response.status_code == 422


def test_cards_cannot_be_edited_while_the_set_is_generating(world):
    set_id = make_set(world, status=MediaStatus.generating)

    response = client.put(f"/flashcard-sets/{set_id}/cards", json={"cards": [card()]}, headers=headers(world["owner"]))

    assert response.status_code == 409


def test_publish_requires_a_draft_with_cards(world):
    h = headers(world["owner"])
    empty = make_set(world)
    generating = make_set(world, status=MediaStatus.generating, cards=[card()])
    ready = make_set(world, cards=[card()])

    assert client.post(f"/flashcard-sets/{empty}/publish", headers=h).status_code == 409
    assert client.post(f"/flashcard-sets/{generating}/publish", headers=h).status_code == 409
    published = client.post(f"/flashcard-sets/{ready}/publish", headers=h)
    assert published.status_code == 200 and published.json()["published_at"] is not None

    # Editing a published set keeps it published; unpublishing hides it again.
    edited = client.put(f"/flashcard-sets/{ready}/cards", json={"cards": [card("Q", "changed")]}, headers=h)
    assert edited.json()["published_at"] is not None
    assert client.post(f"/flashcard-sets/{ready}/unpublish", headers=h).json()["published_at"] is None


def test_listing_and_deleting(world):
    h = headers(world["owner"])
    set_id = make_set(world, cards=[card(), card()])

    listed = client.get(f"/courses/{world['course']}/flashcard-sets", headers=h).json()
    assert [(s["id"], s["card_count"], s["status"]) for s in listed] == [(str(set_id), 2, "draft")]
    assert client.get(f"/flashcard-sets/{set_id}", headers=h).json()["cards"][0]["front"] == "Q"

    assert client.delete(f"/flashcard-sets/{set_id}", headers=h).status_code == 200
    assert client.get(f"/flashcard-sets/{set_id}", headers=h).status_code == 404


def test_another_lecturer_gets_404_everywhere(world):
    set_id = make_set(world, cards=[card()])
    h = headers(world["other"])
    missing = uuid.uuid4()

    for method, path, kwargs in [
        ("POST", "/flashcard-sets", {"params": {"document_id": str(world["doc"])}, "json": {"output_language": "vi"}}),
        ("GET", f"/courses/{world['course']}/flashcard-sets", {}),
        ("GET", f"/flashcard-sets/{set_id}", {}),
        ("PUT", f"/flashcard-sets/{set_id}/cards", {"json": {"cards": []}}),
        ("POST", f"/flashcard-sets/{set_id}/publish", {}),
        ("POST", f"/flashcard-sets/{set_id}/unpublish", {}),
        ("DELETE", f"/flashcard-sets/{set_id}", {}),
    ]:
        assert client.request(method, path, headers=h, **kwargs).status_code == 404, (method, path)
    assert client.get(f"/flashcard-sets/{missing}", headers=h).json() == client.get(
        f"/flashcard-sets/{set_id}", headers=h).json()


def test_a_learner_cannot_call_author_routes(world):
    set_id = make_set(world, cards=[card()])
    h = headers(world["learner"])

    for method, path, kwargs in [
        ("POST", "/flashcard-sets", {"params": {"document_id": str(world["doc"])}, "json": {"output_language": "vi"}}),
        ("GET", f"/courses/{world['course']}/flashcard-sets", {}),
        ("GET", f"/flashcard-sets/{set_id}", {}),
        ("PUT", f"/flashcard-sets/{set_id}/cards", {"json": {"cards": []}}),
        ("POST", f"/flashcard-sets/{set_id}/publish", {}),
        ("POST", f"/flashcard-sets/{set_id}/unpublish", {}),
        ("DELETE", f"/flashcard-sets/{set_id}", {}),
    ]:
        assert client.request(method, path, headers=h, **kwargs).status_code == 403, (method, path)
