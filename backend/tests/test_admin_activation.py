"""Deactivation is the only account removal action and preserves the Moodle link."""
import logging
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import User
from tests.auth_helpers import STRONG_PASSWORD, create_and_login

client = TestClient(app)


def account(*, admin=False):
    return create_and_login(client, f"activation-{uuid.uuid4().hex}@example.com", is_admin=admin)


def headers(login):
    return {"Authorization": f"Bearer {login['access_token']}"}


def test_deactivation_retains_identity_and_reactivation_restores_data(caplog):
    admin, user = account(admin=True), account()
    uid = user['user']['id']
    course = client.post('/courses', headers=headers(user), json={'name': 'Preserved'}).json()
    moodle_id = 10_000 + uuid.uuid4().int % 10**9
    with SessionLocal() as db:
        stored = db.get(User, uuid.UUID(uid))
        stored.moodle_user_id = moodle_id
        db.commit()
    with caplog.at_level(logging.INFO):
        response = client.post(f'/admin/users/{uid}/disable', headers=headers(admin))
    assert response.status_code == 200
    assert client.get('/courses', headers=headers(user)).status_code == 401
    assert client.post('/auth/refresh', json={'refresh_token': user['refresh_token']}).status_code == 401
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(uid)).moodle_user_id == moodle_id
    rows = client.get('/admin/users?is_active=false', headers=headers(admin)).json()
    assert rows and all(not row['is_active'] for row in rows)
    assert any(row['id'] == uid for row in rows)
    active_rows = client.get('/admin/users?is_active=true', headers=headers(admin)).json()
    assert all(row['is_active'] for row in active_rows)
    assert client.post(f'/admin/users/{uid}/enable', headers=headers(admin)).status_code == 200
    login = client.post('/auth/login', json={'email': user['user']['email'], 'password': STRONG_PASSWORD})
    assert login.status_code == 200
    assert any(row['id'] == course['id'] for row in client.get('/courses', headers=headers(login.json())).json())
    assert client.delete(f'/admin/users/{uid}', headers=headers(admin)).status_code == 404
    assert 'admin_disable_user' in caplog.text


def test_no_admin_can_be_deactivated_and_lecturers_cannot_toggle():
    admin, other, user = account(admin=True), account(admin=True), account()
    for target in [admin, other]:
        assert client.post(f"/admin/users/{target['user']['id']}/disable", headers=headers(admin)).status_code == 409
    assert client.post(f"/admin/users/{user['user']['id']}/disable", headers=headers(user)).status_code == 404


def test_deactivated_email_stays_registered_with_reactivation_hint():
    admin, user = account(admin=True), account()
    assert client.post(f"/admin/users/{user['user']['id']}/disable", headers=headers(admin)).status_code == 200
    payload = {'email': user['user']['email'], 'name': 'New Account', 'password': STRONG_PASSWORD}
    for path, auth in [('/auth/register', {}), ('/admin/users', headers(admin))]:
        response = client.post(path, json=payload, headers=auth)
        assert response.status_code == 409
        assert 'Email đã được đăng ký' in response.json()['detail']
        assert 'admin' in response.json()['detail'] and 'kích hoạt lại' in response.json()['detail']
