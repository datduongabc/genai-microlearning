"""Moodle capability, not app claims or role names, controls course linking."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import User
from app.moodle import client as moodle
from tests.auth_helpers import create_and_login

client = TestClient(app)


@pytest.fixture
def linked(monkeypatch):
    login = create_and_login(client, f'permissions-{uuid.uuid4().hex}@example.com')
    headers = {'Authorization': 'Bearer ' + login['access_token']}
    with SessionLocal() as db:
        db.get(User, uuid.UUID(login['user']['id'])).moodle_user_id = 10_000 + uuid.uuid4().int % 10**9
        db.commit()
    state = {'exists': True, 'email': login['user']['email'], 'courses': [{'id': 72, 'fullname': 'Teacher course', 'shortname': 'T72'}]}
    monkeypatch.setattr(moodle, 'teacher_courses', lambda user_id: state)
    def forbidden(*args, **kwargs):
        pytest.fail('App must not discover all courses or enroll users')
    for name in ['list_courses', 'get_course', 'enrol_as_teacher', 'create_course']:
        monkeypatch.setattr(moodle, name, forbidden, raising=False)
    return headers, state


def create(headers):
    return client.post('/courses', headers=headers, json={'name': 'Owned course'}).json()['id']


def test_list_and_link_only_current_teacher_courses(linked):
    headers, state = linked
    assert client.get('/courses/moodle/courses', headers=headers).json() == state['courses']
    cid = create(headers)
    assert client.post(f'/courses/{cid}/link-moodle', headers=headers, json={'moodle_course_id': 73}).status_code == 403
    assert client.post(f'/courses/{cid}/link-moodle', headers=headers, json={'moodle_course_id': 72}).status_code == 200
    state['courses'] = []
    assert client.post(f'/courses/{cid}/link-moodle', headers=headers, json={'moodle_course_id': 72}).status_code == 403
    assert client.get(f'/courses/{cid}/moodle-sections', headers=headers).status_code == 403


def test_more_than_one_app_course_can_link_same_authorized_moodle_course(linked):
    headers, _ = linked
    for _ in range(2):
        cid = create(headers)
        assert client.post(f'/courses/{cid}/link-moodle', headers=headers, json={'moodle_course_id': 72}).status_code == 200
    assert client.post(f'/courses/{cid}/create-moodle', headers=headers).status_code == 404


def test_outage_and_stale_identity_do_not_grant_course_access(linked, monkeypatch):
    headers, state = linked
    cid = create(headers)
    def unavailable(user_id):
        raise moodle.MoodleError('Moodle unavailable')
    monkeypatch.setattr(moodle, 'teacher_courses', unavailable)
    assert client.post(f'/courses/{cid}/link-moodle', headers=headers, json={'moodle_course_id': 72}).status_code == 502
    monkeypatch.setattr(moodle, 'teacher_courses', lambda user_id: {**state, 'suspended': True, 'courses': []})
    assert client.post(f'/courses/{cid}/link-moodle', headers=headers, json={'moodle_course_id': 72}).status_code == 403
    assert client.get('/moodle/account', headers=headers).json()['status'] == 'linked'
    monkeypatch.setattr(moodle, 'teacher_courses', lambda user_id: {**state, 'exists': False, 'courses': []})
    assert client.post(f'/courses/{cid}/link-moodle', headers=headers, json={'moodle_course_id': 72}).status_code == 422


def test_app_course_ownership_is_still_enforced_and_sections_require_a_link(linked):
    headers, _ = linked
    cid = create(headers)
    stranger = create_and_login(client, f'stranger-{uuid.uuid4().hex}@example.com')
    stranger_headers = {'Authorization': 'Bearer ' + stranger['access_token']}
    assert client.post(f'/courses/{cid}/link-moodle', headers=stranger_headers, json={'moodle_course_id': 72}).status_code == 404
    assert client.get(f'/courses/{cid}/moodle-sections', headers=stranger_headers).status_code == 404
    assert client.get(f'/courses/{cid}/moodle-sections', headers=headers).status_code == 400


def test_unlinked_moodle_account_cannot_link_a_course(linked):
    headers, _ = linked
    cid = create(headers)
    with SessionLocal() as db:
        from app.models import Course
        owner = db.get(Course, uuid.UUID(cid)).owner_id
        db.get(User, owner).moodle_user_id = None
        db.commit()
    assert client.post(f'/courses/{cid}/link-moodle', headers=headers, json={'moodle_course_id': 72}).status_code == 422
