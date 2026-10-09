import uuid
from io import BytesIO
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import ComponentType, Course, Document, Module, ModuleComponent, User


@pytest.fixture
def module():
    with SessionLocal() as db:
        user = User(email=f"slides-{uuid.uuid4()}@example.com", name="T",
                    hashed_password="x", is_admin=False)
        db.add(user)
        db.flush()
        course = Course(name="Slides", owner_id=user.id)
        db.add(course)
        db.flush()
        document = Document(course_id=course.id, filename="slides.txt")
        db.add(document)
        db.flush()
        module = Module(course_id=course.id, document_id=document.id, title='Bài giảng "A/B"')
        db.add(module)
        db.flush()
        ids = user.id, course.id, module.id
        db.commit()
    yield ids[2], {"Authorization": f"Bearer {create_access_token(ids[0])}"}
    with SessionLocal() as db:
        db.query(Course).filter(Course.id == ids[1]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids[0]).delete(synchronize_session=False)
        db.commit()


def test_exports_slide_titles_and_bullets(module):
    module_id, headers = module
    with SessionLocal() as db:
        db.add(ModuleComponent(module_id=module_id, type=ComponentType.slides, content={
            "slides": [
                {"title": "Khái niệm", "bullets": ["Ý đầu", "Ý thứ hai"]},
                {"title": "Ứng dụng", "bullets": ["Ví dụ"]},
            ],
        }))
        db.commit()

    response = TestClient(app).get(f"/modules/{module_id}/slides.pptx", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert unquote(response.headers["content-disposition"]) == (
        'attachment; filename*=UTF-8\'\'Bài giảng "A/B".pptx'
    )
    deck = Presentation(BytesIO(response.content))
    assert len(deck.slides) == 2
    assert [slide.shapes.title.text for slide in deck.slides] == ["Khái niệm", "Ứng dụng"]
    assert [slide.placeholders[1].text for slide in deck.slides] == ["Ý đầu\nÝ thứ hai", "Ví dụ"]
