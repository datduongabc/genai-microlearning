"""The lecturer's routes for flashcard sets: generate, edit, publish, delete."""
import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api import flashcards as flashcards_api
from app.auth.rate_limit import limiter
from app.auth.security import create_access_token
from app.config import settings
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
    JobStatus,
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
    monkeypatch.setattr(catalog, "is_available", lambda alias: alias in {"gemini-flash", "claude-fast"})
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
    with SessionLocal() as db:
        card_set = FlashcardSet(
            course_id=world["course"], document_id=world["doc"], output_language="en", model_alias="gemini-flash",
            status=status, cards=cards or [], published_at=datetime.now(UTC) if published else None)
        db.add(card_set)
        db.commit()
        return card_set.id


def card(front="Q", back="A", citations=None, **extra):
    return {"front": front, "back": back, "citations": citations or [], **extra}


def test_generating_reserves_one_generation_and_queues_a_job(world):
    response = client.post("/flashcard-sets", params={"document_id": str(world["doc"])},
                           json={"focus": "heaps", "output_language": "en", "model_alias": "gemini-flash"},
                           headers=headers(world["owner"]))

    assert response.status_code == 200
    with SessionLocal() as db:
        card_set = db.get(FlashcardSet, uuid.UUID(response.json()["id"]))
        job = db.get(Job, uuid.UUID(response.json()["job_id"]))
        assert (card_set.status, card_set.focus, card_set.model_alias) == (MediaStatus.generating, "heaps", "gemini-flash")
        assert (job.job_type, job.document_id, job.payload) == (
            JobType.flashcard_generation, world["doc"], {"flashcard_set_id": str(card_set.id)})
        assert db.get(User, world["owner"]).generation_count == 1


def test_the_daily_generation_limit_names_flashcard_sets_not_modules(world, monkeypatch):
    monkeypatch.setattr(settings, "non_teacher_daily_generation_limit", 1)
    params, h = {"document_id": str(world["doc"])}, headers(world["owner"])
    assert client.post("/flashcard-sets", params=params, json={"output_language": "vi", "focus": "a"}, headers=h).status_code == 200

    over = client.post("/flashcard-sets", params=params, json={"output_language": "vi", "focus": "b"}, headers=h)

    assert over.status_code == 429
    assert "tối đa 1 bộ flashcard" in over.json()["detail"] and "mô-đun" not in over.json()["detail"]


def test_an_identical_request_while_one_is_running_is_refused_without_a_charge(world):
    url, body = "/flashcard-sets", {"output_language": "vi"}
    params = {"document_id": str(world["doc"])}
    assert client.post(url, params=params, json=body, headers=headers(world["owner"])).status_code == 200
    assert client.post(url, params=params, json=body, headers=headers(world["owner"])).status_code == 409
    with SessionLocal() as db:
        assert db.get(User, world["owner"]).generation_count == 1


def test_a_different_focus_during_generation_is_a_second_set(world):
    params, h = {"document_id": str(world["doc"])}, headers(world["owner"])
    first = client.post("/flashcard-sets", params=params, json={"output_language": "vi", "focus": "arrays"}, headers=h)
    second = client.post("/flashcard-sets", params=params, json={"output_language": "vi", "focus": "lists"}, headers=h)
    third = client.post("/flashcard-sets", params=params, json={"output_language": "vi"}, headers=h)

    assert (first.status_code, second.status_code, third.status_code) == (200, 200, 200)
    assert first.json()["id"] != second.json()["id"]
    with SessionLocal() as db:
        assert db.get(User, world["owner"]).generation_count == 3


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

    response = client.put(f"/flashcard-sets/{set_id}/cards", json={"draft_version": 1, "cards": [card()] * 16},
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
        json={"draft_version": 1, "cards": [
            card("Q1", "A1", [chunk]),                     # untouched: keeps what it had
            card("Q2", "A2 fixed", [chunk]),               # edited: the lecturer's, flag cleared
            card("Q3", "A3", []),                          # source removed: the lecturer's, no warning
            card("New", "Card", [chunk]),
        ]})

    assert response.status_code == 200
    assert [(c["author"], c["flag"]) for c in response.json()["cards"]] == [
        ("model", None), ("lecturer", None), ("lecturer", None), ("lecturer", None)]


def test_an_unchanged_flagged_card_keeps_its_flag(world):
    stored = [{"front": "Q", "back": "A", "citations": [], "author": "model", "flag": "Unsupported"}]
    set_id = make_set(world, cards=stored)

    cards = client.put(f"/flashcard-sets/{set_id}/cards", json={"draft_version": 1, "cards": [card("Q", "A")]},
                       headers=headers(world["owner"])).json()["cards"]

    assert (cards[0]["author"], cards[0]["flag"]) == ("model", "Unsupported")


def test_a_citation_from_another_document_is_rejected(world):
    set_id = make_set(world)

    for citation in (str(world["foreign_chunk"]), str(uuid.uuid4()), "not-a-uuid"):
        response = client.put(f"/flashcard-sets/{set_id}/cards", json={"draft_version": 1, "cards": [card(citations=[citation])]},
                              headers=headers(world["owner"]))
        assert response.status_code == 422


def test_cards_cannot_be_edited_while_the_set_is_generating(world):
    set_id = make_set(world, status=MediaStatus.generating)

    response = client.put(f"/flashcard-sets/{set_id}/cards", json={"draft_version": 1, "cards": [card()]}, headers=headers(world["owner"]))

    assert response.status_code == 409


def test_a_save_made_on_an_older_version_is_refused_and_keeps_the_newer_cards(world):
    """Two tabs open on one set: the second to save holds the version both read."""
    set_id = make_set(world, cards=[card("Q1", "A1"), card("Q2", "A2")])
    h = headers(world["owner"])
    first = client.put(f"/flashcard-sets/{set_id}/cards", headers=h,
                       json={"draft_version": 1, "cards": [card("Q1", "A1 fixed"), card("Q2", "A2")]})
    assert first.status_code == 200 and first.json()["draft_version"] == 2

    second = client.put(f"/flashcard-sets/{set_id}/cards", headers=h,
                        json={"draft_version": 1, "cards": [card("Q1", "A1"), card("Q2", "A2 fixed")]})

    assert second.status_code == 409
    assert "lưu ở nơi khác" in second.json()["detail"]
    assert [c["back"] for c in client.get(f"/flashcard-sets/{set_id}", headers=h).json()["cards"]] == ["A1 fixed", "A2"]


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
    edited = client.put(f"/flashcard-sets/{ready}/cards", json={"draft_version": 1, "cards": [card("Q", "changed")]}, headers=h)
    assert edited.json()["published_at"] is not None
    assert client.post(f"/flashcard-sets/{ready}/unpublish", headers=h).json()["published_at"] is None


def test_listing_and_deleting(world):
    h = headers(world["owner"])
    set_id = make_set(world, cards=[card(), card()])

    listed = client.get(f"/courses/{world['course']}/flashcard-sets", headers=h).json()
    assert [(s["id"], s["card_count"], s["status"]) for s in listed] == [(str(set_id), 2, "draft")]
    assert client.get(f"/flashcard-sets/{set_id}", headers=h).json()["cards"][0]["front"] == "Q"


def test_the_lecturer_sees_the_cost_of_a_set_and_the_learner_does_not(world):
    cost = {"usd": 0.0031, "calls": 2, "priced_calls": 2, "prompt_tokens": 600, "completion_tokens": 85}
    set_id = make_set(world, cards=[card()])
    with SessionLocal() as db:
        db.get(FlashcardSet, set_id).cost = cost
        db.get(FlashcardSet, set_id).published_at = datetime.now(UTC)
        db.commit()
    h = headers(world["owner"])

    assert client.get(f"/flashcard-sets/{set_id}", headers=h).json()["cost"] == cost
    assert client.get(f"/courses/{world['course']}/flashcard-sets", headers=h).json()[0]["cost"] == cost

    assert client.delete(f"/flashcard-sets/{set_id}", headers=h).status_code == 200
    assert client.get(f"/flashcard-sets/{set_id}", headers=h).status_code == 404


def _failed_job(world, set_id):
    with SessionLocal() as db:
        job = Job(job_type=JobType.flashcard_generation, step="generate", document_id=world["doc"],
                  status=JobStatus.failed, attempts=5, last_error="Model từ chối",
                  payload={"flashcard_set_id": str(set_id)})
        db.add(job)
        db.commit()
        return job.id


def _failures(world):
    listed = client.get("/documents", params={"course_id": str(world["course"])}, headers=headers(world["owner"]))
    return next(d for d in listed.json() if d["id"] == str(world["doc"]))["failures"]


def test_deleting_a_failed_set_deletes_its_job_so_the_document_is_no_longer_failed(world):
    set_id, other_id = make_set(world, status=MediaStatus.failed), make_set(world, status=MediaStatus.failed)
    other_job, job_id = _failed_job(world, other_id), _failed_job(world, set_id)
    # Each set's failure is its own: a newer job for one set does not hide the other's.
    assert [f["job_id"] for f in _failures(world)] == [str(job_id), str(other_job)]

    assert client.delete(f"/flashcard-sets/{set_id}", headers=headers(world["owner"])).status_code == 200

    with SessionLocal() as db:
        assert db.get(Job, job_id) is None
        assert db.get(Job, other_job) is not None
    # Deleting one set takes its failure only; the other set's stays.
    assert [(f["job_id"], f["job_type"]) for f in _failures(world)] == [(str(other_job), "flashcard_generation")]


def test_a_retried_set_is_generating_again_so_it_cannot_be_deleted_mid_run(world):
    h = headers(world["owner"])
    set_id = make_set(world, status=MediaStatus.failed)
    job_id = _failed_job(world, set_id)

    assert client.post(f"/jobs/{job_id}/retry", headers=h).status_code == 200

    with SessionLocal() as db:
        assert db.get(FlashcardSet, set_id).status == MediaStatus.generating
    assert client.delete(f"/flashcard-sets/{set_id}", headers=h).status_code == 409


def test_another_lecturer_gets_404_everywhere(world):
    set_id = make_set(world, cards=[card()])
    h = headers(world["other"])
    missing = uuid.uuid4()

    for method, path, kwargs in [
        ("POST", "/flashcard-sets", {"params": {"document_id": str(world["doc"])}, "json": {"output_language": "vi"}}),
        ("GET", f"/courses/{world['course']}/flashcard-sets", {}),
        ("GET", f"/flashcard-sets/{set_id}", {}),
        ("PUT", f"/flashcard-sets/{set_id}/cards", {"json": {"draft_version": 1, "cards": []}}),
        ("POST", f"/flashcard-sets/{set_id}/revise", {"json": {"draft_version": 1, "message": "x"}}),
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
        ("PUT", f"/flashcard-sets/{set_id}/cards", {"json": {"draft_version": 1, "cards": []}}),
        ("POST", f"/flashcard-sets/{set_id}/revise", {"json": {"draft_version": 1, "message": "x"}}),
        ("POST", f"/flashcard-sets/{set_id}/publish", {}),
        ("POST", f"/flashcard-sets/{set_id}/unpublish", {}),
        ("DELETE", f"/flashcard-sets/{set_id}", {}),
    ]:
        assert client.request(method, path, headers=h, **kwargs).status_code == 403, (method, path)


def test_a_card_comes_back_with_the_passages_it_cites_and_the_set_with_its_course(world):
    with SessionLocal() as db:
        db.get(Chunk, world["chunk"]).source_ref = json.dumps({"page": 3})
        db.commit()
    chunk = str(world["chunk"])
    gone = str(uuid.uuid4())
    set_id = make_set(world, cards=[
        {"front": "Q", "back": "A", "citations": [chunk, gone], "author": "model", "flag": None},
        {"front": "Q2", "back": "A2", "citations": [], "author": "model", "flag": None},
    ])

    body = client.get(f"/flashcard-sets/{set_id}", headers=headers(world["owner"])).json()

    assert body["course_id"] == str(world["course"])
    assert body["cards"][0]["sources"] == [
        {"chunk_id": chunk, "locator": "x.txt · trang 3", "content": "A heap is a tree.",
         "snippet": "A heap is a tree."}]
    assert body["cards"][1]["sources"] == []
    summary = client.get(f"/courses/{world['course']}/flashcard-sets", headers=headers(world["owner"])).json()[0]
    assert summary["course_id"] == str(world["course"])


def _revise_model(monkeypatch, cards, reply="Đã sửa."):
    """The model returns `cards` as (front, back, sources); the judge flags only a back saying WRONG."""
    from app.generation import fact_check
    from app.generation import flashcards as generation
    seen = {}

    def complete(prompt, schema, **kwargs):
        seen["prompt"] = prompt
        return schema(reply=reply, cards=[{"front": f, "back": b, "sources": s} for f, b, s in cards])
    monkeypatch.setattr(generation, "complete_structured", complete)
    monkeypatch.setattr(generation, "retrieve", lambda *a, **k: [])
    monkeypatch.setattr(fact_check, "judge_alias", lambda alias: "claude-fast")
    monkeypatch.setattr(fact_check, "check_against_sources",
                        lambda items, alias, language: ["Sai nguồn" if "WRONG" in text else None for text, _ in items])
    return seen


def test_a_set_is_revised_in_words_keeping_the_cards_the_request_left_alone(world, monkeypatch):
    chunk = str(world["chunk"])
    kept = {"front": "Q1", "back": "A1", "citations": [chunk], "author": "lecturer", "flag": None}
    set_id = make_set(world, cards=[kept, card("Q2", "A2", [chunk], author="model", flag=None)])
    seen = _revise_model(monkeypatch, [("Q1", "A1", ["S1"]), ("Q2 ngắn", "A2", ["S1"]), ("Q3", "WRONG", ["S1"]),
                                       ("Q4", "No source", [])])

    response = client.post(f"/flashcard-sets/{set_id}/revise", headers=headers(world["owner"]),
                           json={"draft_version": 1, "message": "Rút gọn thẻ 2, thêm một thẻ"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Đã sửa." and body["changed"] is True and body["draft_version"] == 2
    # The untouched card is the same card; a rewritten one is the model's and checked; one without a source is dropped.
    assert [(c["front"], c["author"], c["flag"]) for c in body["cards"]] == [
        ("Q1", "lecturer", None), ("Q2 ngắn", "model", None), ("Q3", "model", "Sai nguồn")]
    assert "A heap is a tree." in seen["prompt"] and "SOURCES: ['S1']" in seen["prompt"]


def test_a_revision_that_changes_nothing_keeps_the_version_and_says_why(world, monkeypatch):
    chunk = str(world["chunk"])
    set_id = make_set(world, cards=[card("Q1", "A1", [chunk], author="model", flag=None)])
    _revise_model(monkeypatch, [("Q1", "A1", ["S1"])], reply="Tài liệu không có nội dung này.")

    body = client.post(f"/flashcard-sets/{set_id}/revise", headers=headers(world["owner"]),
                       json={"draft_version": 1, "message": "Thêm thẻ về đồ thị"}).json()

    assert body["changed"] is False and body["draft_version"] == 1 and body["reply"] == "Tài liệu không có nội dung này."


def test_a_revision_on_an_older_version_or_one_emptying_the_set_is_refused(world, monkeypatch):
    chunk = str(world["chunk"])
    set_id = make_set(world, cards=[card("Q1", "A1", [chunk], author="model", flag=None)])
    _revise_model(monkeypatch, [])
    h = headers(world["owner"])

    assert client.post(f"/flashcard-sets/{set_id}/revise", headers=h,
                       json={"draft_version": 0, "message": "x"}).status_code == 409
    emptied = client.post(f"/flashcard-sets/{set_id}/revise", headers=h, json={"draft_version": 1, "message": "Xóa hết"})
    assert emptied.status_code == 422
    assert client.get(f"/flashcard-sets/{set_id}", headers=h).json()["cards"][0]["front"] == "Q1"


def test_a_save_while_the_model_revises_is_not_written_over(world, monkeypatch):
    chunk = str(world["chunk"])
    set_id = make_set(world, cards=[card("Q1", "A1", [chunk], author="model", flag=None)])
    _revise_model(monkeypatch, [("Q1 sửa", "A1", ["S1"])])
    from app.generation import flashcards as generation
    revise = generation.revise_flashcards

    def save_meanwhile(db, card_set, request):
        # Another tab saves while the model is still writing.
        with SessionLocal() as other:
            saved = other.get(FlashcardSet, set_id)
            saved.cards = [{"front": "Saved", "back": "In the other tab", "citations": [chunk], "author": "lecturer", "flag": None}]
            saved.draft_version = 2
            other.commit()
        return revise(db, card_set, request)
    monkeypatch.setattr(flashcards_api, "revise_flashcards", save_meanwhile)

    response = client.post(f"/flashcard-sets/{set_id}/revise", headers=headers(world["owner"]),
                           json={"draft_version": 1, "message": "Sửa thẻ 1"})

    assert response.status_code == 409
    assert client.get(f"/flashcard-sets/{set_id}", headers=headers(world["owner"])).json()["cards"][0]["front"] == "Saved"


def test_changes_asked_in_words_are_counted_a_day(world, monkeypatch):
    chunk = str(world["chunk"])
    set_id = make_set(world, cards=[card("Q1", "A1", [chunk], author="model", flag=None)])
    _revise_model(monkeypatch, [("Q1", "A1", ["S1"])])
    monkeypatch.setattr(settings, "edits_per_day", 1)
    h = headers(world["owner"])

    assert client.post(f"/flashcard-sets/{set_id}/revise", headers=h, json={"draft_version": 1, "message": "x"}).status_code == 200
    refused = client.post(f"/flashcard-sets/{set_id}/revise", headers=h, json={"draft_version": 1, "message": "x"})
    assert refused.status_code == 429 and "tối đa 1 lần" in refused.json()["detail"]
