"""Open registration, local credentials."""
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import User
from tests.auth_helpers import STRONG_PASSWORD, admin_headers

client = TestClient(app)


def register():
    email = f"open-{uuid.uuid4().hex}@gmail.com"
    response = client.post('/auth/register', json={'email': email, 'name': 'Local User', 'password': STRONG_PASSWORD})
    assert response.status_code == 201
    assert response.json()['is_admin'] is False
    return email, response.json()['id']


def test_any_email_can_register_and_log_in_locally_without_moodle():
    email, user_id = register()
    response = client.post('/auth/login', json={'email': email.upper(), 'password': STRONG_PASSWORD})
    assert response.status_code == 200
    assert response.json()['user']['id'] == user_id
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(user_id)).moodle_user_id is None
    assert client.post('/auth/register', json={'email': email, 'name': 'Duplicate', 'password': STRONG_PASSWORD}).status_code == 409


def test_admin_creates_local_non_admin_without_link_and_cannot_create_admin():
    email = f"created-{uuid.uuid4().hex}@gmail.com"
    response = client.post('/admin/users', headers=admin_headers(), json={
        'email': email, 'name': 'Created User', 'password': STRONG_PASSWORD, 'is_admin': True,
    })
    assert response.status_code == 201
    assert response.json()['is_admin'] is False
    assert response.json()['moodle_status'] == 'not_linked'
    assert client.post('/auth/login', json={'email': email, 'password': STRONG_PASSWORD}).status_code == 200


def test_registration_can_be_closed_and_says_whom_to_ask(monkeypatch):
    """A public deployment closes self registration: every account would get the daily generation
    quota, so anyone could create accounts to spend the provider credit. Accounts then come from the
    administrator or from an LMS launch."""
    from app.config import settings

    assert client.get('/auth/options').json() == {'self_registration': True}
    monkeypatch.setattr(settings, 'self_registration', False)
    assert client.get('/auth/options').json() == {'self_registration': False}

    email = f"closed-{uuid.uuid4().hex}@gmail.com"
    response = client.post('/auth/register', json={'email': email, 'name': 'Closed', 'password': STRONG_PASSWORD})
    assert response.status_code == 403
    assert 'quản trị viên' in response.json()['detail']
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == email).count() == 0

    # The administrator can still create accounts.
    created = client.post('/admin/users', headers=admin_headers(), json={
        'email': email, 'name': 'Created', 'password': STRONG_PASSWORD})
    assert created.status_code == 201
