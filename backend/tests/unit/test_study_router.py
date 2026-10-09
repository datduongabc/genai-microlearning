import uuid

from fastapi.testclient import TestClient

from app.main import app


def test_study_routes_require_authentication_before_database_access():
    client = TestClient(app)
    sid = uuid.uuid4()
    for method, path, body in (("GET", "/studies", None), ("GET", f"/studies/{sid}", None),
                               ("POST", "/studies", {"name": "S", "rubric_version": "v1"}),
                               ("POST", f"/studies/{sid}/items", {"human_packets": []})):
        assert client.request(method, path, json=body).status_code == 401
