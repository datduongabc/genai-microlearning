"""A Moodle email request remains inactive until its owner confirms inside Moodle."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import User
from app.moodle import client as moodle_client
from tests.auth_helpers import create_and_login

client = TestClient(app)


@pytest.fixture
def confirmed_link_service(monkeypatch):
    state = {'status': 'pending', 'moodle_user_id': 10_000 + uuid.uuid4().int % 10**9, 'email': 'teacher@moodle.example'}
    monkeypatch.setattr(moodle_client, 'request_account_link', lambda **kwargs: {
        'request_id': uuid.uuid4().hex, 'confirmation_url': 'http://moodle.example/local/lms_bridge/confirm.php',
    }, raising=False)
    monkeypatch.setattr(moodle_client, 'account_link_status', lambda **kwargs: state.copy(), raising=False)
    monkeypatch.setattr(moodle_client, 'teacher_courses', lambda user_id: {
        'exists': True, 'email': state['email'], 'courses': [],
    }, raising=False)
    return state


def account():
    email = f'confirmation-{uuid.uuid4().hex}@gmail.com'
    data = create_and_login(client, email)
    return data['user']['id'], {'Authorization': 'Bearer ' + data['access_token']}


def request(headers):
    return client.post('/moodle/account/link-requests', headers=headers, json={'email': 'teacher@moodle.example'})


def stored_link(user_id):
    with SessionLocal() as db:
        return db.get(User, uuid.UUID(user_id)).moodle_user_id


def test_email_request_is_pending_and_does_not_link_before_moodle_confirmation(confirmed_link_service):
    user_id, headers = account()
    response = request(headers)
    assert response.status_code == 202
    assert response.json()['status'] == 'pending'
    assert stored_link(user_id) is None
    polled = client.get('/moodle/account', headers=headers)
    assert polled.status_code == 200 and polled.json()['status'] == 'pending'
    assert stored_link(user_id) is None


def test_confirmed_request_links_only_the_requesting_app_account(confirmed_link_service):
    user_id, headers = account()
    _, stranger = account()
    assert request(headers).status_code == 202
    confirmed_link_service['status'] = 'confirmed'
    assert client.get('/moodle/account', headers=stranger).json()['status'] == 'not_linked'
    response = client.get('/moodle/account', headers=headers)
    assert response.status_code == 200 and response.json()['status'] == 'linked'
    assert response.json()['email'] == confirmed_link_service['email']
    assert stored_link(user_id) == confirmed_link_service['moodle_user_id']


@pytest.mark.parametrize('status', ['rejected', 'expired'])
def test_rejected_or_expired_request_never_links(confirmed_link_service, status):
    user_id, headers = account()
    assert request(headers).status_code == 202
    confirmed_link_service['status'] = status
    response = client.get('/moodle/account', headers=headers)
    assert response.status_code == 200 and response.json()['status'] == status
    assert stored_link(user_id) is None


def test_one_moodle_account_cannot_link_two_app_accounts(confirmed_link_service):
    _, first = account()
    second_id, second = account()
    assert request(first).status_code == 202
    assert request(second).status_code == 202
    confirmed_link_service['status'] = 'confirmed'
    assert client.get('/moodle/account', headers=first).json()['status'] == 'linked'
    assert client.get('/moodle/account', headers=second).status_code == 409
    assert stored_link(second_id) is None
    assert client.get('/moodle/account', headers=second).json()['status'] == 'not_linked'
    assert request(second).status_code == 202


def test_link_request_requires_authentication_and_a_valid_email(confirmed_link_service):
    assert client.post('/moodle/account/link-requests', json={'email': 'teacher@moodle.example'}).status_code == 401
    _, headers = account()
    assert client.post('/moodle/account/link-requests', headers=headers, json={'email': 'not an email'}).status_code == 422


def test_stale_identity_releases_link_but_a_moodle_outage_retains_it(confirmed_link_service, monkeypatch):
    user_id, headers = account()
    assert request(headers).status_code == 202
    confirmed_link_service['status'] = 'confirmed'
    assert client.get('/moodle/account', headers=headers).json()['status'] == 'linked'
    def unavailable(user_id):
        raise moodle_client.MoodleError('Unavailable')
    monkeypatch.setattr(moodle_client, 'teacher_courses', unavailable)
    assert client.get('/moodle/account', headers=headers).status_code == 502
    assert stored_link(user_id) is not None
    monkeypatch.setattr(moodle_client, 'teacher_courses', lambda user_id: {'exists': False, 'email': '', 'courses': []})
    assert client.get('/moodle/account', headers=headers).json()['status'] == 'not_linked'
    assert stored_link(user_id) is None


def test_suspended_moodle_identity_keeps_link(confirmed_link_service, monkeypatch):
    user_id, headers = account()
    assert request(headers).status_code == 202
    confirmed_link_service['status'] = 'confirmed'
    assert client.get('/moodle/account', headers=headers).json()['status'] == 'linked'
    monkeypatch.setattr(moodle_client, 'teacher_courses', lambda user_id: {
        'exists': True, 'suspended': True, 'email': confirmed_link_service['email'], 'courses': [],
    })
    assert client.get('/moodle/account', headers=headers).json()['status'] == 'linked'
    assert stored_link(user_id) == confirmed_link_service['moodle_user_id']


def test_unknown_moodle_email_creates_no_pending_request(confirmed_link_service, monkeypatch):
    def unknown(**kwargs):
        raise moodle_client.MoodleError('Unknown', errorcode='invaliduser')
    monkeypatch.setattr(moodle_client, 'request_account_link', unknown)
    user_id, headers = account()
    assert request(headers).status_code == 422
    assert stored_link(user_id) is None
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(user_id)).moodle_request_id is None
