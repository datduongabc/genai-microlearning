import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.auth_helpers import create_and_login
from tests.upload_helpers import upload_document

client = TestClient(app)


def _create_and_login(email: str) -> str:
    return create_and_login(client, email)["access_token"]


def test_user_cannot_see_or_use_another_users_course():
    suffix = uuid.uuid4().hex[:8]
    token_owner = _create_and_login(f"owner-{suffix}@example.com")
    token_other = _create_and_login(f"other-{suffix}@example.com")

    course = client.post(
        "/courses", json={"name": f"Course {suffix}"}, headers={"Authorization": f"Bearer {token_owner}"}
    ).json()
    course_id = course["id"]

    # A course must not appear in another user's course list.
    other_courses = client.get("/courses", headers={"Authorization": f"Bearer {token_other}"}).json()
    assert all(c["id"] != course_id for c in other_courses)

    # ...and that user must not be able to upload into it, even knowing its id.
    response = upload_document(
        client, course_id, "x.html", b"<p>hello</p>", {"Authorization": f"Bearer {token_other}"},
    )
    assert response.status_code == 404

    # The owner still sees and can use their own course.
    owner_courses = client.get("/courses", headers={"Authorization": f"Bearer {token_owner}"}).json()
    assert any(c["id"] == course_id for c in owner_courses)


def test_only_the_owner_renames_a_course_and_the_name_is_cleaned():
    suffix = uuid.uuid4().hex[:8]
    owner = {"Authorization": f"Bearer {_create_and_login(f'rename-owner-{suffix}@example.com')}"}
    other = {"Authorization": f"Bearer {_create_and_login(f'rename-other-{suffix}@example.com')}"}
    course_id = client.post("/courses", json={"name": "Cũ"}, headers=owner).json()["id"]

    assert client.patch(f"/courses/{course_id}", json={"name": "Lạ"}, headers=other).status_code == 404
    assert client.patch(f"/courses/{course_id}", json={"name": "   "}, headers=owner).status_code == 422
    renamed = client.patch(f"/courses/{course_id}", json={"name": "  Hệ   điều hành "}, headers=owner)
    assert renamed.status_code == 200 and renamed.json()["name"] == "Hệ điều hành"
    assert [c["name"] for c in client.get("/courses", headers=owner).json() if c["id"] == course_id] == ["Hệ điều hành"]
