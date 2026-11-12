import json
import uuid
from types import SimpleNamespace

import pytest

from app.citations import citation_label
from app.db import SessionLocal
from app.generation.video_script import Scene
from app.models import Chunk, Course, Document, DocumentStatus, User
from app.video.sources import scene_source_labels, source_label


@pytest.mark.parametrize(("locators", "lang", "label"), [
    (['{"page": 9}', '{"page": 10}'], "vi", "Nguồn: Chapter 5.pdf, trang 9–10"),
    (['{"page": 10}', '{"spans": [{"page": 3}, {"page": 9}]}'], "vi", "Nguồn: Chapter 5.pdf, trang 3, 9–10"),
    (['{"page": 9}', '{"page": 10}'], "en", "Source: Chapter 5.pdf, pages 9–10"),
    (['{"page": 4}'], "en", "Source: Chapter 5.pdf, page 4"),
    (['{"slide_index": 3, "slide_title": "BFS"}', '{"slide_index": 4}'], "vi", "Nguồn: Chapter 5.pdf, slide 3–4"),
    (['{"start_ms": 65000, "end_ms": 70000}', '{"start_ms": 70000, "end_ms": 125500}'], "vi",
     "Nguồn: Chapter 5.pdf, 01:05–02:05"),
    # A heading names one chunk's section, not the slide's, so the one line at the foot leaves it out.
    (['{"page": 2, "heading": "FCFS"}'], "vi", "Nguồn: Chapter 5.pdf, trang 2"),
    (["", "not json"], "vi", "Nguồn: Chapter 5.pdf"),
    ([], "vi", ""),
])
def test_source_label_merges_the_pages_of_every_cited_passage(locators, lang, label):
    assert source_label("Chapter 5.pdf", locators, lang) == label


def test_citation_label_is_still_importable_from_the_modules_api():
    from app.api.modules import citation_label as from_api

    assert from_api is citation_label
    assert citation_label("x.pdf", '{"page": 2}') == "x.pdf · trang 2"


@pytest.fixture
def chunks():
    with SessionLocal() as db:
        user = User(email=f"src-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Sources", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(course_id=course.id, filename="Chapter 5_CPU Scheduling.pdf", status=DocumentStatus.embedded)
        db.add(doc)
        db.flush()
        rows = [Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=f"c{i}",
                      source_ref=json.dumps({"page": page})) for i, page in enumerate([10, 9, 14])]
        db.add_all(rows)
        db.commit()
        ids = (user.id, course.id, doc.id, [str(c.id) for c in rows])
    yield ids
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids[1]).delete()
        db.query(User).filter(User.id == ids[0]).delete()
        db.commit()


def _scene(citations) -> Scene:
    return Scene(kind="summary", title="t", bullets=["a", "b"], narration="n.", citations=citations)


def test_each_scene_gets_the_line_of_the_passages_it_cites(chunks):
    _, _, doc_id, chunk_ids = chunks
    video = SimpleNamespace(document_id=doc_id, output_language="vi")
    scenes = [_scene(chunk_ids[:2]), _scene([chunk_ids[2]]), _scene([]),
              _scene(["not-a-uuid", str(uuid.uuid4())])]
    with SessionLocal() as db:
        labels = scene_source_labels(db, video, scenes)
    assert labels == [
        "Nguồn: Chapter 5_CPU Scheduling.pdf, trang 9–10",
        "Nguồn: Chapter 5_CPU Scheduling.pdf, trang 14",
        "",
        "",
    ]
