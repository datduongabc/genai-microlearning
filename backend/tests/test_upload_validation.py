import uuid

from fastapi.testclient import TestClient

from app.api.documents import MAX_UPLOAD_SIZE_BYTES
from app.main import app
from tests.auth_helpers import STRONG_PASSWORD

client = TestClient(app)


def _register_and_login(email: str) -> str:
    client.post("/auth/register", json={"email": email, "name": "Test", "password": STRONG_PASSWORD})
    response = client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    return response.json()["access_token"]


def _make_course(token: str, suffix: str) -> str:
    course = client.post(
        "/courses", json={"name": f"Upload validation test {suffix}"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    return course["id"]


def test_upload_rejects_unsupported_extension():
    suffix = uuid.uuid4().hex[:8]
    token = _register_and_login(f"upload-ext-{suffix}@example.com")
    course_id = _make_course(token, suffix)

    response = client.post(
        "/documents", params={"course_id": course_id},
        files={"file": ("virus.exe", b"not a document", "application/octet-stream")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_upload_rejects_oversized_file():
    suffix = uuid.uuid4().hex[:8]
    token = _register_and_login(f"upload-size-{suffix}@example.com")
    course_id = _make_course(token, suffix)

    oversized = b"x" * (MAX_UPLOAD_SIZE_BYTES + 1)
    response = client.post(
        "/documents", params={"course_id": course_id},
        files={"file": ("big.txt", oversized, "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 413
