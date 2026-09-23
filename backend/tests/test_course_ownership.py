import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.auth_helpers import STRONG_PASSWORD

client = TestClient(app)


def _register_and_login(email: str) -> str:
    client.post("/auth/register", json={"email": email, "name": "Test", "password": STRONG_PASSWORD})
    response = client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    return response.json()["access_token"]


def test_user_cannot_see_or_use_another_users_course():
    suffix = uuid.uuid4().hex[:8]
    token_owner = _register_and_login(f"owner-{suffix}@example.com")
    token_other = _register_and_login(f"other-{suffix}@example.com")

    course = client.post(
        "/courses", json={"name": f"Course {suffix}"}, headers={"Authorization": f"Bearer {token_owner}"}
    ).json()
    course_id = course["id"]

    # A course must not appear in another user's course list.
    other_courses = client.get("/courses", headers={"Authorization": f"Bearer {token_other}"}).json()
    assert all(c["id"] != course_id for c in other_courses)

    # ...and that user must not be able to upload into it, even knowing its id.
    response = client.post(
        "/documents",
        params={"course_id": course_id},
        files={"file": ("x.txt", b"hello", "text/plain")},
        headers={"Authorization": f"Bearer {token_other}"},
    )
    assert response.status_code == 404

    # The owner still sees and can use their own course.
    owner_courses = client.get("/courses", headers={"Authorization": f"Bearer {token_owner}"}).json()
    assert any(c["id"] == course_id for c in owner_courses)
