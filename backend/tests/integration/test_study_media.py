"""Flashcard sets and videos as study packets: added by the admin, rated by an assigned rater on
their own criteria, and shown in the results apart from the RAG comparison."""
import uuid

from sqlalchemy import select

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.models import Document, FlashcardSet, MediaStatus, Video
from tests.integration.test_studies import client, create_study, world  # noqa: F401

CARDS = [{"front": "What is X?", "back": "X is a concept.", "sources": ["S1"]}]
SCENES = [{"kind": "text", "title": "X", "bullets": ["X is a concept"], "narration": "X is a concept."}]


def _media(world, **video):  # noqa: F811
    with SessionLocal() as db:
        doc = db.scalar(select(Document).where(Document.course_id == world["course"]))
        cards = FlashcardSet(document_id=doc.id, course_id=world["course"], focus="Định nghĩa", output_language="vi",
                             model_alias="test-model", status=MediaStatus.draft, cards=CARDS)
        clip = Video(document_id=doc.id, course_id=world["course"], output_language="vi", model_alias="test-model",
                     voice_alias="voice", image_alias="image", status=MediaStatus.ready, scenes=SCENES, **video)
        writing = FlashcardSet(document_id=doc.id, course_id=world["course"], output_language="vi",
                               model_alias="test-model", status=MediaStatus.generating, cards=[])
        db.add_all([cards, clip, writing])
        db.commit()
        return str(cards.id), str(clip.id)


def _rating(code, criteria):
    return {"anon_id": code, "scores": {c: {"score": 4, "justification": "ok"} for c in criteria},
            "hallucination_findings": [], "overall_notes": ""}


def test_the_admin_adds_a_flashcard_set_and_a_video_and_an_assigned_rater_rates_each(world):  # noqa: F811
    cards_id, video_id = _media(world, video_key="videos/x.mp4")
    sid = create_study(world)
    path = f"/studies/{sid}/media"

    offered = client.get(f"/studies/{sid}/media-candidates", headers=world["headers"]).json()
    # The set still being written is not offered: there is nothing to rate in it.
    assert sorted((o["kind"], o["state"]) for o in offered) == [("flashcards", "ready"), ("video", "ready")]
    assert client.post(path, headers=world["headers"], json={"kind": "flashcards", "id": cards_id}).status_code == 201
    items = client.post(path, headers=world["headers"], json={"kind": "video", "id": video_id}).json()
    assert client.post(path, headers=world["headers"], json={"kind": "video", "id": video_id}).status_code == 409
    assert client.post(path, headers=world["teacher_headers"], json={"kind": "video", "id": video_id}).status_code == 404
    assert sorted(i["anon_code"] for i in items) == ["F01", "V01"]
    assert {o["state"] for o in client.get(f"/studies/{sid}/media-candidates", headers=world["headers"]).json()} == {"in_study"}

    with SessionLocal() as db:
        from app.models import User
        email = db.get(User, world["teacher"]).email
    client.put(f"/studies/{sid}/raters", headers=world["headers"],
               json={"emails": [email], "assignments": {email: [i["id"] for i in items]}}).raise_for_status()
    mine = client.get(f"/studies/mine/{sid}/items", headers=world["teacher_headers"]).json()["items"]
    by_kind = {i["kind"]: i for i in mine}
    assert by_kind["flashcards"]["packet"]["cards"] == [{"front": "What is X?", "back": "X is a concept."}]
    assert by_kind["video"]["packet"]["has_video"] is True

    flash, clip = by_kind["flashcards"], by_kind["video"]
    rate = f"/studies/mine/{sid}/items/{{}}/rating"
    # A module rating on a flashcard set, or the video's criteria on it, is refused.
    wrong = _rating(flash["anon_code"], ["accuracy", "focus", "coherence", "visuals_match"])
    assert client.put(rate.format(flash["id"]), headers=world["teacher_headers"], json=wrong).status_code == 422
    saved = client.put(rate.format(flash["id"]), headers=world["teacher_headers"],
                       json=_rating(flash["anon_code"], ["accuracy", "focus", "clarity", "no_duplicates"]))
    assert saved.status_code == 200, saved.text
    assert client.put(rate.format(clip["id"]), headers=world["teacher_headers"],
                      json=_rating(clip["anon_code"], ["accuracy", "focus", "coherence", "visuals_match"])).status_code == 200
    again = client.get(f"/studies/mine/{sid}/items", headers=world["teacher_headers"]).json()
    assert again["rated"] == 2

    results = client.get(f"/studies/{sid}/results", headers=world["headers"]).json()
    shown = {i["group"]: i for i in results["items"]}
    assert shown["flashcards"]["criteria"] == ["accuracy", "focus", "clarity", "no_duplicates"]
    assert shown["video"]["ratings"][0]["scores"]["visuals_match"]["score"] == 4
    # The rater files feed the RAG comparison, which these packets are not part of.
    csv = client.get(f"/studies/{sid}/results/raters/1.csv", headers=world["headers"]).text
    assert csv.strip().count("\n") == 0

    link = client.get(f"/studies/mine/{sid}/items/{clip['id']}/video", headers=world["teacher_headers"])
    assert link.status_code == 200 and link.json()["filename"] == f"{clip['anon_code']}.mp4"
    admin = {"Authorization": "Bearer " + create_access_token(world["admin"])}
    # The admin is not a rater of it: the blind view does not give them the video.
    assert client.get(f"/studies/mine/{sid}/items/{clip['id']}/video", headers=admin).status_code == 404


def test_a_video_not_rendered_has_no_video_to_play(world):  # noqa: F811
    _, video_id = _media(world)
    sid = create_study(world)
    items = client.post(f"/studies/{sid}/media", headers=world["headers"], json={"kind": "video", "id": video_id}).json()
    assert items[0]["packet"]["has_video"] is False
    assert uuid.UUID(items[0]["id"])
