"""An environment with no Moodle address (AWS with an LMS that only launches the tool by LTI):
the Moodle features say they are unavailable instead of failing as if Moodle were down."""
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import User
from app.moodle import client as moodle_client
from app.moodle.errors import MOODLE_NOT_CONFIGURED
from tests.auth_helpers import create_and_login

client = TestClient(app)
REAL_TEACHER_COURSES = moodle_client.teacher_courses


@pytest.fixture
def no_moodle(monkeypatch):
    monkeypatch.setattr(settings, 'moodle_base_url', '')
    sent = []
    monkeypatch.setattr(moodle_client.httpx, 'post', lambda *a, **kw: sent.append(a) or pytest.fail('no request'))
    return sent


def headers():
    data = create_and_login(client, f'nomoodle-{uuid.uuid4().hex}@example.com')
    return {'Authorization': 'Bearer ' + data['access_token']}


def test_the_account_is_unavailable_not_an_error(no_moodle):
    response = client.get('/moodle/account', headers=headers())
    assert response.status_code == 200
    assert response.json()['status'] == 'unavailable'
    assert no_moodle == []


def test_a_link_request_says_moodle_is_not_connected(no_moodle):
    response = client.post('/moodle/account/link-requests', headers=headers(), json={'email': 'a@moodle.example'})
    assert response.status_code == 503
    assert response.json()['detail'] == MOODLE_NOT_CONFIGURED
    assert no_moodle == []


def test_html_moodle_response_returns_502_from_the_api(monkeypatch):
    login = create_and_login(client, f"moodle-html-{uuid.uuid4().hex}@example.com")
    from app.db import SessionLocal

    with SessionLocal() as db:
        db.get(User, uuid.UUID(login["user"]["id"])).moodle_user_id = 10_000 + uuid.uuid4().int % 10**9
        db.commit()
    monkeypatch.setattr(moodle_client, "teacher_courses", REAL_TEACHER_COURSES)
    monkeypatch.setattr(
        moodle_client.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(
            200, text="<html>unavailable</html>", request=httpx.Request("POST", url)
        ),
    )
    response = client.get(
        "/courses/moodle/courses", headers={"Authorization": "Bearer " + login["access_token"]}
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "Thao tác với Moodle thất bại, vui lòng thử lại sau."}
