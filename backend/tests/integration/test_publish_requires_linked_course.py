import uuid

from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Course, Document, Module, User

client = TestClient(app)


def test_publish_to_moodle_refuses_when_course_not_linked():
    # Arrange a module belonging to an app course that has never been linked
    # to Moodle (moodle_course_id is None) -- directly via the DB, and a
    # token minted directly (not via /auth/login, which is IP-rate-limited
    # and shared across the whole test suite).
    suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        user = User(
            email=f"publish-{suffix}@example.com", name="Test", hashed_password="x", is_admin=False, moodle_user_id=10_000 + uuid.uuid4().int % 10**9
        )
        db.add(user)
        db.flush()
        course = Course(name=f"Course {suffix}", owner_id=user.id)
        db.add(course)
        db.flush()
        document = Document(course_id=course.id, filename="x.pdf")
        db.add(document)
        db.flush()
        module = Module(document_id=document.id, course_id=course.id, title="Test Module")
        db.add(module)
        db.commit()
        token = create_access_token(user.id)
        module_id = module.id
    finally:
        db.close()

    response = client.post(
        f"/modules/{module_id}/publish-to-moodle", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400
