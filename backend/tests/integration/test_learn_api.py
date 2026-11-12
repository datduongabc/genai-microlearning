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


def test_the_learn_routes_need_a_login(world):
    assert client.get(f"/learn/courses/{world['course']}/items").status_code == 401
