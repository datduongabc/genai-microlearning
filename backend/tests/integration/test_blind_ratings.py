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
              'slide_quality', 'quiz_quality', 'bloom_fidelity', 'query_alignment', 'examples_feedback')


def evaluation(code):
    # The test packets have one quiz question and the practice question, each with one flaw at most, so
    # the computed quiz_quality is 5 whatever is sent; it is sent as 5 so the saved rating reads back as sent.
    return {'anon_id': code, **{key: {'score': 4, 'justification': 'Supported by source'}
                               for key in DIMENSIONS}, 'quiz_quality': {'score': 5, 'justification': 'One flaw'},
            'quiz_flaws': [{'question': 1, 'flaws': ['vague_terms']}, {'question': 2, 'flaws': []}],
            'overall_notes': 'Good',
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
    # Invited and given every pair: a rater given none sees nothing (alembic 0045).
    keys = sorted({i['pair_id'] or i['id'] for i in items})
    result = client.put(f'/studies/{sid}/raters', headers=world['headers'],
                        json={'emails': emails, 'assignments': {email: keys for email in emails}})
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
    assert response.json() == {'emails': [email], 'assignments': {}}
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
        assert set(item) == {'id', 'anon_code', 'kind', 'packet', 'rating', 'reference'}
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


def test_quiz_quality_is_computed_from_the_flaws_of_every_question(blind):
    sid, item = blind['sid'], blind['items'][0]
    path = f"/studies/mine/{sid}/items/{item['id']}/rating"
    body = evaluation(item['anon_code'])
    # Two flaws on one of the two questions: half the questions are acceptable, so 3, whatever is sent.
    body['quiz_flaws'][0]['flaws'] = ['vague_terms', 'absolute_terms']
    saved = client.put(path, headers=blind['teacher_headers'], json=body)
    assert saved.status_code == 200 and saved.json()['quiz_quality']['score'] == 3
    # Every question of the packet is checked once: one left out, or one that is not there, is refused.
    for flaws in ([{'question': 1}], [{'question': 1}, {'question': 2}, {'question': 3}],
                  [{'question': 1}, {'question': 2, 'flaws': ['not_a_flaw']}]):
        response = client.put(path, headers=blind['teacher_headers'], json={**body, 'quiz_flaws': flaws})
        assert response.status_code == 422, flaws


def test_a_study_on_an_older_rubric_takes_no_new_module_rating_and_keeps_its_results(blind):
    sid, item = blind['sid'], blind['items'][0]
    path = f"/studies/mine/{sid}/items/{item['id']}/rating"
    legacy = {key: {'score': 4, 'justification': 'v1'} for key in DIMENSIONS if key != 'examples_feedback'}
    with SessionLocal() as db:
        db.get(Study, uuid.UUID(sid)).rubric_version = 'v1'
        db.add(Rating(study_item_id=uuid.UUID(item['id']), user_id=blind['second'], rater_kind='human',
                      overall_notes='v1 rating', **legacy))
        db.commit()
    refused = client.put(path, headers=blind['teacher_headers'], json=evaluation(item['anon_code']))
    assert refused.status_code == 409 and 'rubric v1' in refused.json()['detail']
    assert client.post(f'/studies/{sid}/judge', headers=blind['headers']).status_code == 409
    results = client.get(f'/studies/{sid}/results', headers=blind['headers']).json()
    assert 'examples_feedback' not in results['criteria'] and len(results['criteria']) == 8
    shown = next(i for i in results['items'] if i['id'] == item['id'])['ratings'][0]
    assert set(shown['scores']) == set(legacy) and shown['quiz_flaws'] == []
    second = client.get(f'/studies/mine/{sid}/items', headers=blind['second_headers']).json()
    mine = next(i for i in second['items'] if i['id'] == item['id'])['rating']
    assert mine['accuracy'] == {'score': 4, 'justification': 'v1'} and 'examples_feedback' not in mine
    assert client.get(f'/studies/{sid}/results/raters/1.csv', headers=blind['headers']).text.splitlines()[0] == (
        'anon_id,' + ','.join(k for k in DIMENSIONS if k != 'examples_feedback') + ',hallucination_notes')


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
                assert set(rows[0]) == {'id', 'study_id', 'anon_code', 'packet', 'reference_file_id', 'media_key'}
            ratings = db.scalars(select(Rating)).all()
            assert len(ratings) == (1 if who == 'teacher' else 0)
            if who != 'teacher':
                assert db.execute(text('UPDATE ratings SET overall_notes=\'stolen\' WHERE id=:id'), {'id': rating_id}).rowcount == 0
                foreign = Rating(study_item_id=uuid.UUID(item['id']), user_id=blind['teacher'], rater_kind='human',
                                 **{key: {'score': 1, 'justification': 'x'} for key in DIMENSIONS}, quiz_flaws=[],
                                 overall_notes='stolen')
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


def test_a_rater_given_some_pairs_sees_and_rates_only_those(blind):
    sid, items = blind['sid'], blind['items']
    teacher = blind['emails'][0]
    pair = next(i for i in items if i['pair_id'] is not None)['pair_id']
    other = next(i for i in items if i['pair_id'] is None)
    path = f'/studies/{sid}/raters'

    given = client.put(path, headers=blind['headers'], json={'emails': blind['emails'], 'assignments': {teacher: [pair]}})

    assert given.status_code == 200, given.text
    assert client.get(path, headers=blind['headers']).json()['assignments'] == {teacher: [pair]}
    mine = client.get(f'/studies/mine/{sid}/items', headers=blind['teacher_headers']).json()
    # Both packets of the pair, nothing else; the second rater, now given nothing, sees nothing.
    assert mine['total'] == 2 and len(mine['items']) == 2
    assert client.get(f'/studies/mine/{sid}/items', headers=blind['second_headers']).json()['total'] == 0
    refused = client.put(f"/studies/mine/{sid}/items/{other['id']}/rating", headers=blind['teacher_headers'],
                         json=evaluation(other['anon_code']))
    assert refused.status_code == 404
    totals = {r['email']: r['assigned'] for r in client.get(f'/studies/{sid}/results', headers=blind['headers']).json()['raters']}
    # The rater given nothing has no progress to show.
    assert totals == {teacher: 2}


def test_pairs_go_only_to_invited_raters_and_to_pairs_of_the_study(blind):
    path = f"/studies/{blind['sid']}/raters"
    teacher = blind['emails'][0]
    stranger = client.put(path, headers=blind['headers'], json={'emails': [teacher], 'assignments': {'x@example.com': ['a']}})
    unknown = client.put(path, headers=blind['headers'], json={'emails': [teacher], 'assignments': {teacher: [str(uuid.uuid4())]}})
    assert stranger.status_code == 422 and unknown.status_code == 422


def test_saving_the_invitations_alone_keeps_the_pairs_given(blind):
    path = f"/studies/{blind['sid']}/raters"
    teacher = blind['emails'][0]
    pair = next(i for i in blind['items'] if i['pair_id'] is not None)['pair_id']
    client.put(path, headers=blind['headers'], json={'emails': blind['emails'], 'assignments': {teacher: [pair]}})

    kept = client.put(path, headers=blind['headers'], json={'emails': blind['emails']})
    dropped = client.put(path, headers=blind['headers'], json={'emails': blind['emails'][1:]})

    assert kept.json()['assignments'] == {teacher: [pair]}
    assert dropped.json()['assignments'] == {}
