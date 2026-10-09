"""Account unlink releases an identity while preserving Moodle course links."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import ComponentType, Course, Document, Module, ModuleComponent, User
from app.moodle import client as moodle_client
from tests.auth_helpers import admin_headers, create_and_login

client = TestClient(app)


@pytest.fixture
def service(monkeypatch):
    state = {'status': 'pending', 'moodle_user_id': 10_000 + uuid.uuid4().int % 10**9}
    monkeypatch.setattr(moodle_client, 'request_account_link', lambda **kw: {'request_id': uuid.uuid4().hex})
    monkeypatch.setattr(moodle_client, 'account_link_status', lambda **kw: state.copy())
    monkeypatch.setattr(moodle_client, 'teacher_courses', lambda uid: {
        'exists': True, 'email': 'teacher@moodle.example', 'courses': [],
    })
    return state


def account():
    data = create_and_login(client, f'unlink-{uuid.uuid4().hex}@example.com')
    return uuid.UUID(data['user']['id']), {'Authorization': 'Bearer ' + data['access_token']}


def request(headers):
    return client.post('/moodle/account/link-requests', headers=headers, json={'email': 'teacher@moodle.example'})


def assert_cleared(user_id):
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert (user.moodle_user_id, user.moodle_request_id, user.moodle_request_email) == (None, None, None)


@pytest.mark.parametrize('admin_unlink', [False, True])
def test_unlink_clears_all_fields_and_is_idempotent(service, admin_unlink):
    user_id, headers = account()
    assert request(headers).status_code == 202
    with SessionLocal() as db:
        db.get(User, user_id).moodle_user_id = service['moodle_user_id']
        db.commit()
    auth = admin_headers() if admin_unlink else headers
    for _ in range(2):
        response = (client.post(f'/admin/users/{user_id}/moodle-unlink', headers=auth)
                    if admin_unlink else client.delete('/moodle/account', headers=auth))
        assert response.status_code == 200
        assert_cleared(user_id)
    service['status'] = 'confirmed'
    assert client.get('/moodle/account', headers=headers).json()['status'] == 'not_linked'


@pytest.mark.parametrize('same_account', [False, True])
def test_identity_can_relink_after_unlink(service, same_account):
    first_id, first = account()
    second_id, second = account()
    assert request(first).status_code == 202
    service['status'] = 'confirmed'
    assert client.get('/moodle/account', headers=first).json()['status'] == 'linked'
    assert client.delete('/moodle/account', headers=first).status_code == 200
    target = first if same_account else second
    service['status'] = 'pending'
    assert request(target).status_code == 202
    assert client.get('/moodle/account', headers=target).json()['status'] == 'pending'
    service['status'] = 'confirmed'
    assert client.get('/moodle/account', headers=target).json()['status'] == 'linked'
    with SessionLocal() as db:
        assert db.get(User, first_id if same_account else second_id).moodle_user_id == service['moodle_user_id']
    if not same_account:
        assert_cleared(first_id)


def test_unlink_requires_auth_and_admin_route_refuses_non_admin(service):
    target_id, target = account()
    _, stranger = account()
    assert request(target).status_code == 202
    service['status'] = 'confirmed'
    assert client.get('/moodle/account', headers=target).json()['status'] == 'linked'
    assert client.delete('/moodle/account').status_code == 401
    assert client.post(f'/admin/users/{target_id}/moodle-unlink', headers=stranger).status_code == 404
    with SessionLocal() as db:
        assert db.get(User, target_id).moodle_user_id == service['moodle_user_id']
    assert client.post(f'/admin/users/{uuid.uuid4()}/moodle-unlink', headers=admin_headers()).status_code == 404


@pytest.mark.parametrize('admin_unlink', [False, True])
def test_unlink_discards_pending_consent(service, admin_unlink):
    user_id, headers = account()
    assert request(headers).status_code == 202
    response = (client.post(f'/admin/users/{user_id}/moodle-unlink', headers=admin_headers())
                if admin_unlink else client.delete('/moodle/account', headers=headers))
    assert response.status_code == 200
    service['status'] = 'confirmed'
    assert client.get('/moodle/account', headers=headers).json()['status'] == 'not_linked'
    assert_cleared(user_id)


@pytest.mark.parametrize('with_content', [False, True])
def test_course_link_survives_unlink_and_publish_explains_missing_account(service, with_content):
    user_id, headers = account()
    assert request(headers).status_code == 202
    service['status'] = 'confirmed'
    assert client.get('/moodle/account', headers=headers).json()['status'] == 'linked'
    with SessionLocal() as db:
        course = Course(name='Preserved Moodle link', owner_id=user_id, moodle_course_id=123)
        db.add(course)
        db.flush()
        document = Document(course_id=course.id, filename='unlink.pdf')
        db.add(document)
        db.flush()
        module = Module(course_id=course.id, document_id=document.id, title='Unlink test')
        db.add(module)
        db.flush()
        if with_content:
            db.add(ModuleComponent(module_id=module.id, type=ComponentType.content, content={'markdown': 'Content to publish'}))
        db.commit()
        course_id, module_id = course.id, module.id
    assert client.delete('/moodle/account', headers=headers).status_code == 200
    with SessionLocal() as db:
        assert db.get(Course, course_id).moodle_course_id == 123
    response = client.post(f'/modules/{module_id}/publish-to-moodle', headers=headers)
    assert response.status_code == 422
    assert 'chưa liên kết tài khoản Moodle' in response.json()['detail']
