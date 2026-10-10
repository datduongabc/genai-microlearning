import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.auth.security import create_access_token
from app.db import RequestSessionLocal, SessionLocal
from app.models import Rating, Study, User
from tests.integration.test_studies import client, create_study, human_packet, load
from tests.integration.test_studies import world as study_world

world = study_world

DIMENSIONS = ('accuracy', 'completeness', 'consistency', 'hallucination_score',
              'slide_quality', 'quiz_quality', 'bloom_fidelity', 'query_alignment')


def evaluation(code):
    return {'anon_id': code, **{key: {'score': 4, 'justification': 'Supported by source'}
                               for key in DIMENSIONS}, 'overall_notes': 'Good',
            'hallucination_findings': [{'claim': 'Unsupported', 'field': 'content', 'reason': 'No source'}]}


@pytest.fixture
def blind(world):
    with SessionLocal() as db:
        second = User(email=f'rater-{uuid.uuid4()}@example.com', name='Second', hashed_password='x')
        outsider = User(email=f'outsider-{uuid.uuid4()}@example.com', name='Outsider', hashed_password='x')
        db.add_all([second, outsider])
        db.commit()
        ids = [second.id, outsider.id]
        emails = [db.get(User, world['teacher']).email, second.email]
    sid = create_study(world)
    items = load(world, sid, module_ids=[str(world['rag'])], human_packets=[human_packet()] * 6).json()
    result = client.put(f'/studies/{sid}/raters', headers=world['headers'], json={'emails': emails})
    assert result.status_code == 200, result.text
    yield {**world, 'sid': sid, 'items': items, 'second': ids[0], 'outsider': ids[1], 'emails': emails,
           'second_headers': {'Authorization': 'Bearer ' + create_access_token(ids[0])},
           'outsider_headers': {'Authorization': 'Bearer ' + create_access_token(ids[1])}}
    with SessionLocal() as db:
        db.query(Study).filter(Study.id == uuid.UUID(sid)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        db.commit()


def test_invitation_validates_active_accounts_and_normalizes_emails(world):
    sid = create_study(world)
    with SessionLocal() as db:
        email = db.get(User, world['teacher']).email
    path = f'/studies/{sid}/raters'
    assert client.put(path, headers=world['headers'], json={'emails': ['missing@example.com']}).status_code == 422
    response = client.put(path, headers=world['headers'], json={'emails': [email.upper(), email]})
    assert response.status_code == 200, response.text
    assert response.json() == {'emails': [email]}
    assert client.get(path, headers=world['headers']).json() == response.json()
    assert client.get(path, headers=world['teacher_headers']).status_code == 404
    with SessionLocal() as db:
        db.get(User, world['teacher']).is_active = False
        db.commit()
    assert client.put(path, headers=world['headers'], json={'emails': [email]}).status_code == 422


def test_blind_items_stable_distinct_orders_and_own_ratings(blind):
    sid = blind['sid']
    path = f'/studies/mine/{sid}/items'
    first = client.get(path, headers=blind['teacher_headers']).json()
    second = client.get(path, headers=blind['second_headers']).json()
    assert first == client.get(path, headers=blind['teacher_headers']).json()
    assert [i['id'] for i in first['items']] != [i['id'] for i in second['items']]
    assert first['rated'] == 0 and first['total'] == 8
    for item in first['items']:
        assert set(item) == {'id', 'anon_code', 'packet', 'rating'}
        assert set(item['packet']) == set(human_packet())
    item = first['items'][0]
    rating_path = f"{path}/{item['id']}/rating"
    body = evaluation(item['anon_code'])
    assert client.put(rating_path, headers=blind['teacher_headers'], json=body).json() == body
    body['accuracy']['score'] = 2
    assert client.put(rating_path, headers=blind['teacher_headers'], json=body).json() == body
    own = client.get(path, headers=blind['teacher_headers']).json()
    assert own['rated'] == 1 and own['items'][0]['rating']['accuracy']['score'] == 2
    assert client.get(path, headers=blind['second_headers']).json()['rated'] == 0
    assert all(i['rating'] is None for i in client.get(path, headers=blind['second_headers']).json()['items'])
    mine = client.get('/studies/mine', headers=blind['teacher_headers']).json()
    assert mine == [{**own['study'], 'rated': 1, 'total': 8}]
    assert load(blind, sid, human_packets=[human_packet()]).status_code == 409
    with SessionLocal() as db:
        assert db.get(Study, uuid.UUID(sid)).status == 'draft'
        assert len(db.scalars(select(Rating).where(Rating.user_id == blind['teacher'])).all()) == 1


def test_uninvited_revoked_inactive_and_foreign_item_access(blind):
    sid = blind['sid']
    path = f'/studies/mine/{sid}/items'
    item = blind['items'][0]
    for suffix in ('', f"/{item['id']}/rating"):
        response = client.request('PUT' if suffix else 'GET', path + suffix,
                                  headers=blind['outsider_headers'], json=evaluation(item['anon_code']) if suffix else None)
        assert response.status_code == 404
    assert client.get('/studies/mine', headers=blind['outsider_headers']).json() == []
    assert client.put(f'{path}/{uuid.uuid4()}/rating', headers=blind['teacher_headers'],
                      json=evaluation(item['anon_code'])).status_code == 404
    assert client.put(f"{path}/{item['id']}/rating", headers=blind['teacher_headers'],
                      json=evaluation('wrong-code')).status_code == 422
    invalid = evaluation(item['anon_code'])
    invalid['accuracy']['score'] = 6
    assert client.put(f"{path}/{item['id']}/rating", headers=blind['teacher_headers'], json=invalid).status_code == 422
    with SessionLocal() as db:
        db.get(User, blind['second']).is_active = False
        db.commit()
    assert client.get(path, headers=blind['second_headers']).status_code == 401
    assert client.put(f'/studies/{sid}/raters', headers=blind['headers'], json={'emails': []}).status_code == 200
    assert client.get(path, headers=blind['teacher_headers']).status_code == 404


def test_database_blind_projection_and_rating_rls(blind):
    sid = blind['sid']
    item = blind['items'][0]
    path = f"/studies/mine/{sid}/items/{item['id']}/rating"
    assert client.put(path, headers=blind['teacher_headers'], json=evaluation(item['anon_code'])).status_code == 200
    with SessionLocal() as db:
        rating_id = db.scalar(select(Rating.id).where(Rating.study_item_id == uuid.UUID(item['id'])))
    for who, count in (('teacher', 8), ('second', 8), ('outsider', 0)):
        with RequestSessionLocal() as db:
            db.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {'uid': str(blind[who])})
            assert db.execute(text('SELECT * FROM study_items')).all() == []
            assert db.execute(text('SELECT * FROM studies')).all() == []
            rows = db.execute(text('SELECT * FROM blind_study_items WHERE study_id=:sid'), {'sid': sid}).mappings().all()
            assert len(rows) == count
            if rows:
                assert set(rows[0]) == {'id', 'study_id', 'anon_code', 'packet'}
            ratings = db.scalars(select(Rating)).all()
            assert len(ratings) == (1 if who == 'teacher' else 0)
            if who != 'teacher':
                assert db.execute(text('UPDATE ratings SET overall_notes=\'stolen\' WHERE id=:id'), {'id': rating_id}).rowcount == 0
                foreign = Rating(study_item_id=uuid.UUID(item['id']), user_id=blind['teacher'], rater_kind='human',
                                 **{key: {'score': 1, 'justification': 'x'} for key in DIMENSIONS}, overall_notes='stolen')
                db.add(foreign)
                with pytest.raises(ProgrammingError):
                    db.flush()
                db.rollback()


def test_closed_study_and_revocation_protect_saved_ratings(blind):
    sid = blind['sid']
    item = blind['items'][0]
    path = f"/studies/mine/{sid}/items/{item['id']}/rating"
    assert client.put(path, headers=blind['teacher_headers'], json=evaluation(item['anon_code'])).status_code == 200
    with SessionLocal() as db:
        db.get(Study, uuid.UUID(sid)).status = 'closed'
        db.commit()
    assert client.put(path, headers=blind['teacher_headers'], json=evaluation(item['anon_code'])).status_code == 409
    with RequestSessionLocal() as db:
        db.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {'uid': str(blind['teacher'])})
        with pytest.raises(IntegrityError) as caught:
            db.execute(text("UPDATE ratings SET overall_notes='changed' WHERE study_item_id=:iid"), {'iid': item['id']})
        assert caught.value.orig.sqlstate == '23514'
        db.rollback()
    assert client.put(f'/studies/{sid}/raters', headers=blind['headers'], json={'emails': []}).status_code == 200
    with RequestSessionLocal() as db:
        db.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {'uid': str(blind['teacher'])})
        assert db.scalars(select(Rating)).all() == []
        assert db.execute(text('SELECT * FROM blind_studies')).all() == []
        assert db.execute(text('SELECT * FROM blind_study_items WHERE study_id=:sid'), {'sid': sid}).all() == []
