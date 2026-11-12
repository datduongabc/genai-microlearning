"""What a learner (a member of a course) reads of the lecturer's published flashcard sets and videos."""
import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Chunk, Course, CourseMember, Document, DocumentStatus, FlashcardSet, MediaStatus, User
from app.models.media import Video
from app.storage import S3UploadStore

client = TestClient(app)


def headers(user_id):
    return {"Authorization": "Bearer " + create_access_token(user_id)}


@pytest.fixture
def world():
    with SessionLocal() as db:
        def user(name):
            u = User(email=f"learn-{name}-{uuid.uuid4().hex[:8]}@example.com", name=name, hashed_password="x",
                     can_author=name in ("owner", "outsider"))
            db.add(u)
            db.flush()
            return u
        owner, member, outsider = user("owner"), user("member"), user("outsider")
        course = Course(name="Learn", owner_id=owner.id)
        elsewhere = Course(name="Elsewhere", owner_id=outsider.id)
        db.add_all([course, elsewhere])
        db.flush()
        doc = Document(course_id=course.id, filename="bai-giang.pdf", status=DocumentStatus.embedded, raw_text="x")
        db.add(doc)
        db.flush()
        chunk = Chunk(document_id=doc.id, course_id=course.id, chunk_index=0, content="A heap is a tree.",
                      source_ref=json.dumps({"page": 2}))
        db.add_all([chunk, CourseMember(course_id=course.id, user_id=member.id),
                    CourseMember(course_id=elsewhere.id, user_id=outsider.id)])
        db.flush()

        def card_set(published, focus):
            s = FlashcardSet(
                course_id=course.id, document_id=doc.id, output_language="vi", model_alias="gemini-fast",
                focus=focus, status=MediaStatus.draft, published_at=datetime.now(UTC) if published else None,
                cards=[{"front": "Heap?", "back": "A tree.", "citations": [str(chunk.id)], "author": "model",
                        "flag": "Chưa có nguồn"}])
            db.add(s)
            db.flush()
            return s
        published, draft = card_set(True, "Heap"), card_set(False, "Nháp")
        video = Video(course_id=course.id, document_id=doc.id, output_language="vi", model_alias="gemini-fast",
                      voice_alias="voice-heygen", image_alias="image-nano-banana", focus="Video đã giao",
                      status=MediaStatus.ready, published_at=datetime.now(UTC), duration_seconds=61.5)
        hidden_video = Video(course_id=course.id, document_id=doc.id, output_language="vi", model_alias="gemini-fast",
                             voice_alias="voice-heygen", image_alias="image-nano-banana", focus="Video nháp",
                             status=MediaStatus.draft)
        db.add_all([video, hidden_video])
        db.commit()
        ids = {"owner": owner.id, "member": member.id, "outsider": outsider.id, "course": course.id, "doc": doc.id,
               "published": published.id, "draft": draft.id, "video": video.id, "elsewhere": elsewhere.id}
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id.in_([ids["course"], ids["elsewhere"]])).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([ids["owner"], ids["member"], ids["outsider"]])).delete(
            synchronize_session=False)
        db.commit()


def test_a_member_lists_only_published_items(world):
    response = client.get(f"/learn/courses/{world['course']}/items", headers=headers(world["member"]))

    assert response.status_code == 200
    body = response.json()
    assert [s["id"] for s in body["flashcard_sets"]] == [str(world["published"])]
    assert body["flashcard_sets"][0] | {} == {
        "id": str(world["published"]), "focus": "Heap", "card_count": 1,
        "document": {"id": str(world["doc"]), "filename": "bai-giang.pdf"},
    }
    assert body["videos"] == [{
        "id": str(world["video"]), "focus": "Video đã giao", "duration_seconds": 61.5,
        "document": {"id": str(world["doc"]), "filename": "bai-giang.pdf"},
    }]


def test_the_owner_previewing_sees_only_published_items(world):
    # The owner's row level security shows drafts too, so the route itself must leave them out.
    body = client.get(f"/learn/courses/{world['course']}/items", headers=headers(world["owner"])).json()

    assert [s["id"] for s in body["flashcard_sets"]] == [str(world["published"])]
    assert [v["id"] for v in body["videos"]] == [str(world["video"])]
    assert client.get(f"/learn/flashcard-sets/{world['draft']}", headers=headers(world["owner"])).status_code == 404


def test_an_unpublished_set_is_404_for_a_member(world):
    response = client.get(f"/learn/flashcard-sets/{world['draft']}", headers=headers(world["member"]))

    assert response.status_code == 404
    assert response.json() == client.get(f"/learn/flashcard-sets/{uuid.uuid4()}", headers=headers(world["member"])).json()


def test_a_member_of_another_course_gets_404(world):
    for path in (f"/learn/courses/{world['course']}/items", f"/learn/flashcard-sets/{world['published']}"):
        assert client.get(path, headers=headers(world["outsider"])).status_code == 404


def test_the_learner_view_has_no_review_flags(world):
    response = client.get(f"/learn/flashcard-sets/{world['published']}", headers=headers(world["member"]))

    assert response.status_code == 200
    body = response.json()
    assert body["cards"] == [{"front": "Heap?", "back": "A tree.", "sources": ["bai-giang.pdf · trang 2"]}]
    assert "flag" not in response.text and "author" not in response.text and "A heap is a tree." not in response.text


def test_a_source_cited_twice_by_a_card_is_shown_once(world):
    with SessionLocal() as db:
        card_set = db.get(FlashcardSet, world["published"])
        cited = card_set.cards[0]["citations"][0]
        card_set.cards = [{**card_set.cards[0], "citations": [cited, cited]}]
        db.commit()

    body = client.get(f"/learn/flashcard-sets/{world['published']}", headers=headers(world["member"])).json()

    assert body["cards"][0]["sources"] == ["bai-giang.pdf · trang 2"]


def test_the_learn_routes_need_a_login(world):
    assert client.get(f"/learn/courses/{world['course']}/items").status_code == 401


def _render(world, **changes):
    """The video as a render leaves it: a file, and the scenes as they were rendered."""
    with SessionLocal() as db:
        video = db.get(Video, world["video"])
        chunk = db.query(Chunk).filter(Chunk.document_id == world["doc"]).one()
        video.video_key = "videos/x/v1.mp4"
        video.rendered_version = 1
        video.rendered_scenes = [
            {"title": "Mở đầu", "start_seconds": 0.0, "citations": [str(chunk.id), str(chunk.id)]},
            {"title": "Heap", "start_seconds": 30.5, "citations": []},
        ]
        # The draft is newer than the render, with other titles: learners follow the render.
        video.scenes = [{"title": "Bản nháp mới", "narration": "x", "citations": []}]
        for name, value in changes.items():
            setattr(video, name, value)
        db.commit()


def test_a_member_gets_a_short_lived_link_to_the_rendered_video(world, monkeypatch):
    _render(world)
    monkeypatch.setattr("app.api.videos.media_store", lambda: _S3())

    response = client.get(f"/learn/videos/{world['video']}", headers=headers(world["member"]))

    assert response.status_code == 200
    assert response.json() == {
        "id": str(world["video"]), "course_id": str(world["course"]), "title": "Video đã giao",
        "duration_seconds": 61.5, "file_url": "https://s3.example/videos/x/v1.mp4?sig=1",
        "scenes": [
            {"title": "Mở đầu", "start_seconds": 0.0, "sources": ["bai-giang.pdf · trang 2"]},
            {"title": "Heap", "start_seconds": 30.5, "sources": []},
        ],
    }


def test_without_s3_the_link_is_the_learner_content_route(world):
    _render(world)
    h = headers(world["member"])

    body = client.get(f"/learn/videos/{world['video']}", headers=h).json()

    assert body["file_url"] == f"/learn/videos/{world['video']}/file/content"
    # Fetched with the same token, and answering like the detail does when it must not.
    assert client.get(body["file_url"], headers=h).status_code in (200, 404)
    assert client.get(body["file_url"], headers=headers(world["outsider"])).status_code == 404


def test_an_unpublished_or_foreign_video_is_404_and_gives_no_link(world):
    _render(world)
    with SessionLocal() as db:
        hidden = db.query(Video).filter(Video.course_id == world["course"], Video.published_at.is_(None)).one()
        hidden.video_key = "videos/x/hidden.mp4"
        hidden.rendered_scenes = []
        unrendered = Video(course_id=world["course"], document_id=world["doc"], output_language="vi",
                           model_alias="gemini-fast", voice_alias="voice-heygen", image_alias="image-nano-banana",
                           status=MediaStatus.draft, published_at=datetime.now(UTC))
        db.add(unrendered)
        db.commit()
        hidden_id, unrendered_id = hidden.id, unrendered.id
    missing = client.get(f"/learn/videos/{uuid.uuid4()}", headers=headers(world["member"]))

    for who, video_id in (("member", hidden_id), ("owner", hidden_id), ("member", unrendered_id),
                          ("outsider", world["video"])):
        for path in (f"/learn/videos/{video_id}", f"/learn/videos/{video_id}/file/content"):
            response = client.get(path, headers=headers(world[who]))
            assert response.status_code == 404, (who, path)
            assert response.json() == missing.json()
            assert "file_url" not in response.text and ".mp4" not in response.text


class _S3(S3UploadStore):
    def __init__(self):
        pass

    def presigned_get(self, key, filename):
        return f"https://s3.example/{key}?sig=1"
