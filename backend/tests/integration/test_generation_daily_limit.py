"""Accepted module requests consume an account's durable UTC daily allowance."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.auth.rate_limit import limiter
from app.auth.security import create_access_token
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Course, Document, DocumentStatus, User
from app.moodle import client as moodle

client = TestClient(app)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(limiter, 'enabled', False)
    with SessionLocal() as db:
        user = User(email=f'quota-{uuid.uuid4().hex}@example.com', name='Quota', hashed_password='x')
        db.add(user)
        db.flush()
        course = Course(name='Quota course', owner_id=user.id)
        db.add(course)
        db.flush()
        documents = [Document(course_id=course.id, filename=f'{i}.txt',
                              status=DocumentStatus.embedded, raw_text='Parsed text') for i in range(5)]
        db.add_all(documents)
        db.commit()
        return user.id, course.id, [doc.id for doc in documents]


def generate(world, index):
    user, _, docs = world
    return client.post('/modules/generate', params={'document_id': str(docs[index])},
                       headers={'Authorization': 'Bearer ' + create_access_token(user)})


def test_fourth_generation_is_refused_even_on_another_document(world):
    for index in range(3):
        assert generate(world, index).status_code == 200
    response = generate(world, 3)
    assert response.status_code == 429
    assert '3' in response.json()['detail']


def test_rejected_duplicate_job_does_not_consume_daily_allowance(world):
    assert generate(world, 0).status_code == 200
    assert generate(world, 0).status_code == 409
    assert generate(world, 1).status_code == 200
    assert generate(world, 2).status_code == 200


def test_teacher_needs_an_app_link_to_a_currently_authorized_moodle_course(world, monkeypatch):
    uid, cid, _ = world
    with SessionLocal() as db:
        db.get(User, uid).moodle_user_id = 10_000 + uuid.uuid4().int % 10**9
        db.get(Course, cid).moodle_course_id = 101
        db.commit()
    state = {'exists': True, 'email': 'teacher@example.com', 'courses': [{'id': 101}]}
    monkeypatch.setattr(moodle, 'teacher_courses', lambda user_id: state)
    for index in range(4):
        assert generate(world, index).status_code == 200


def test_suspended_moodle_teacher_uses_non_teacher_allowance_and_keeps_link(world, monkeypatch):
    uid, cid, _ = world
    with SessionLocal() as db:
        db.get(User, uid).moodle_user_id = moodle_user_id = 10_000 + uuid.uuid4().int % 10**9
        db.get(Course, cid).moodle_course_id = 101
        db.commit()
    monkeypatch.setattr(moodle, 'teacher_courses', lambda user_id: {
        'exists': True, 'suspended': True, 'email': 'teacher@example.com', 'courses': [],
    })
    for index in range(3):
        assert generate(world, index).status_code == 200
    assert generate(world, 3).status_code == 429
    with SessionLocal() as db:
        assert db.get(User, uid).moodle_user_id == moodle_user_id


def test_moodle_outage_uses_non_teacher_allowance(world, monkeypatch):
    uid, cid, _ = world
    with SessionLocal() as db:
        db.get(User, uid).moodle_user_id = 10_000 + uuid.uuid4().int % 10**9
        db.get(Course, cid).moodle_course_id = 102
        db.commit()
    def unavailable(user_id):
        raise moodle.MoodleError('Unavailable')
    monkeypatch.setattr(moodle, 'teacher_courses', unavailable)
    for index in range(3):
        assert generate(world, index).status_code == 200
    assert generate(world, 3).status_code == 429


def test_next_utc_day_resets_the_counter_and_configured_limit_applies(world, monkeypatch):
    from datetime import timedelta

    from app.config import settings
    from app.generation.limits import today
    monkeypatch.setattr(settings, 'non_teacher_daily_generation_limit', 1)
    assert generate(world, 0).status_code == 200
    assert generate(world, 1).status_code == 429
    with SessionLocal() as db:
        user = db.get(User, world[0])
        user.generation_day = today() - timedelta(days=1)
        db.commit()
    assert generate(world, 1).status_code == 200
    assert generate(world, 2).status_code == 429


def test_moodle_course_capability_without_local_course_link_is_still_limited(world, monkeypatch):
    with SessionLocal() as db:
        db.get(User, world[0]).moodle_user_id = 10_000 + uuid.uuid4().int % 10**9
        db.commit()
    monkeypatch.setattr(moodle, 'teacher_courses', lambda user_id: {'exists': True, 'courses': [{'id': 101}]})
    for index in range(3):
        assert generate(world, index).status_code == 200
    assert generate(world, 3).status_code == 429


def test_admin_does_not_bypass_daily_allowance(world):
    with SessionLocal() as db:
        db.get(User, world[0]).is_admin = True
        db.commit()
    for index in range(3):
        assert generate(world, index).status_code == 200
    assert generate(world, 3).status_code == 429


def test_concurrent_documents_cannot_overdraw_daily_allowance(world):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(lambda index: generate(world, index), range(5)))
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 429, 429]
    with SessionLocal() as db:
        assert db.get(User, world[0]).generation_count == 3




def test_control_generation_consumes_one_module_allowance(world):
    from app.models import Module
    uid, cid, docs = world
    with SessionLocal() as db:
        paired = Module(document_id=docs[0], course_id=cid, title='Paired', learning_objective='Understand concepts',
                        bloom_level='understand', sub_objectives=['Describe the concept', 'Explain its purpose'])
        db.add(paired)
        db.commit()
        paired_id = paired.id
    assert generate(world, 1).status_code == 200
    assert generate(world, 2).status_code == 200
    response = client.post('/modules/generate', params={'document_id': str(docs[0])},
                           headers={'Authorization': 'Bearer ' + create_access_token(uid)},
                           json={'use_retrieval': False, 'paired_module_id': str(paired_id)})
    assert response.status_code == 200
    assert generate(world, 3).status_code == 429


def test_invalid_input_does_not_consume_and_retry_does_not_charge_again(world):
    from app.models import Job, JobStatus
    from tests.auth_helpers import admin_headers
    uid, _, docs = world
    response = client.post('/modules/generate', params={'document_id': str(docs[0])},
                           headers={'Authorization': 'Bearer ' + create_access_token(uid)},
                           json={'section_ids': [str(uuid.uuid4())]})
    assert response.status_code == 422
    response = generate(world, 0)
    assert response.status_code == 200
    job_id = response.json()['job_id']
    with SessionLocal() as db:
        job = db.get(Job, uuid.UUID(job_id))
        job.status = JobStatus.failed
        job.attempts = job.max_attempts
        job.system_failure = True  # an outage: what the admin may retry
        db.commit()
        assert db.get(User, uid).generation_count == 1
    response = client.post('/admin/jobs/retry', headers=admin_headers(), json={'job_ids': [job_id]})
    assert response.status_code == 200 and response.json()['retried'] == [job_id]
    with SessionLocal() as db:
        assert db.get(User, uid).generation_count == 1


@pytest.mark.parametrize('path', ['/modules/generate', '/videos', '/flashcard-sets'])
def test_the_moodle_check_runs_with_no_row_locked(world, monkeypatch, path):
    """Moodle can take seconds to answer (4 s when its name does not resolve). A request that held
    the account or the document row meanwhile kept every other request of the lecturer waiting:
    the live run's "Sinh video" took 8 s to come back."""
    uid, cid, docs = world
    with SessionLocal() as db:
        db.get(User, uid).moodle_user_id = 10_000 + uuid.uuid4().int % 10**9
        db.get(Course, cid).moodle_course_id = 101
        db.commit()
    seen = []

    def teacher_courses(user_id):
        with SessionLocal() as other:
            for table, row in (('users', uid), ('documents', docs[0])):
                try:
                    other.execute(text(f'SELECT 1 FROM {table} WHERE id = :id FOR UPDATE NOWAIT'), {'id': row})
                    seen.append((table, 'free'))
                except OperationalError:
                    seen.append((table, 'locked'))
                other.rollback()
        return {'exists': True, 'email': 'teacher@example.com', 'courses': [{'id': 101}]}

    monkeypatch.setattr(moodle, 'teacher_courses', teacher_courses)
    response = generate_at(world, path, 0)
    assert response.status_code == 200, response.text
    assert seen == [('users', 'free'), ('documents', 'free')]


def generate_at(world, path, index):
    user, _, docs = world
    return client.post(path, params={'document_id': str(docs[index])},
                       headers={'Authorization': 'Bearer ' + create_access_token(user)})


def test_a_moodle_linked_teacher_still_has_a_daily_ceiling(world, monkeypatch):
    """No 3-a-day limit, but not unlimited either: a leaked token or a looping script stops here."""
    uid, cid, _ = world
    with SessionLocal() as db:
        db.get(User, uid).moodle_user_id = 10_000 + uuid.uuid4().int % 10**9
        db.get(Course, cid).moodle_course_id = 102
        db.commit()
    monkeypatch.setattr(moodle, 'teacher_courses', lambda user_id: {
        'exists': True, 'email': 'teacher@example.com', 'courses': [{'id': 102}]})
    monkeypatch.setattr(settings, 'full_access_daily_generation_limit', 4)
    for index in range(4):
        assert generate(world, index).status_code == 200
    refused = generate(world, 4)
    assert refused.status_code == 429 and '4' in refused.json()['detail']
