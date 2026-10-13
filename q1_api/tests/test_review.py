from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import sqlite3
from threading import Barrier

from fastapi.testclient import TestClient
import pytest

from app.main import app
from conftest import application, csv_bytes, upload


STATES = ['pending', 'ocr_done', 'ocr_failed', 'stage1_approved',
          'stage1_rejected', 'stage2_approved', 'stage2_rejected', 'exported']
# Expected business rules are literal test data, independent of production rules.
ALLOWED = {
    ('ocr_done', 1, 'approve'): 'stage1_approved',
    ('ocr_done', 1, 'reject'): 'stage1_rejected',
    ('stage1_approved', 2, 'approve'): 'stage2_approved',
    ('stage1_approved', 2, 'reject'): 'stage2_rejected',
}


def make_case(client, database_path, state='ocr_done'):
    batch = upload(client, csv_bytes(application())).json()
    case = client.get(f"/api/batches/{batch['id']}/cases").json()['items'][0]
    # Q1-only fixture, not a production bypass of OCR.
    with sqlite3.connect(database_path) as connection:
        connection.execute('UPDATE cases SET status = ? WHERE id = ?', (state, case['id']))
    return case


def review(client, case_id, stage=1, decision='approve'):
    return client.post(f'/api/cases/{case_id}/review', json={'stage': stage, 'decision': decision})


def stored_reviews(database_path, case_id):
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            'SELECT * FROM reviews WHERE case_id = ? ORDER BY id', (case_id,))]


@pytest.mark.parametrize('state', STATES)
@pytest.mark.parametrize('stage', [1, 2])
@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_review_transition_matrix(client, database_path, state, stage, decision):
    case = make_case(client, database_path, state)
    response = review(client, case['id'], stage, decision)
    expected = ALLOWED.get((state, stage, decision))
    assert response.status_code == (200 if expected else 409)
    current = client.get(f"/api/batches/{case['batch_id']}/cases").json()['items'][0]
    assert current['status'] == (expected or state)
    history = stored_reviews(database_path, case['id'])
    if expected:
        assert response.json()['status'] == expected
        record = response.json()['review']
        assert (record['stage'], record['decision'], record['previous_status'], record['new_status']) == (
            stage, decision, state, expected)
        assert datetime.fromisoformat(record['reviewed_at']).utcoffset().total_seconds() == 0
        assert len(history) == 1
        assert history[0]['new_status'] == expected
    else:
        detail = response.json()['detail']
        assert detail['code'] == 'invalid_transition'
        assert detail['current_status'] == state
        assert detail['stage'] == stage
        assert detail['decision'] == decision
        assert detail['message']
        assert history == []


def test_successful_two_stage_lifecycle_is_persisted(database_path):
    with TestClient(app) as client:
        case = make_case(client, database_path)
        assert review(client, case['id']).json()['status'] == 'stage1_approved'
        assert review(client, case['id'], 2).json()['status'] == 'stage2_approved'
    with TestClient(app) as restarted:
        batch = restarted.get(f"/api/batches/{case['batch_id']}").json()
        assert batch['status_counts']['stage2_approved'] == 1
        assert batch['status_counts']['pending'] == 0
        assert review(restarted, case['id'], 2).status_code == 409
    records = stored_reviews(database_path, case['id'])
    assert [(r['stage'], r['decision']) for r in records] == [(1, 'approve'), (2, 'approve')]


@pytest.mark.parametrize('stage', [1, 2])
def test_rejection_prevents_every_later_review(client, database_path, stage):
    case = make_case(client, database_path)
    if stage == 2:
        assert review(client, case['id']).status_code == 200
    assert review(client, case['id'], stage, 'reject').status_code == 200
    for next_stage in (1, 2):
        for decision in ('approve', 'reject'):
            assert review(client, case['id'], next_stage, decision).status_code == 409
    assert len(stored_reviews(database_path, case['id'])) == stage
    approved = client.get(f"/api/batches/{case['batch_id']}/cases?status=stage2_approved").json()
    assert approved['total'] == 0


@pytest.mark.parametrize('stage', [1, 2])
@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_cannot_repeat_or_reverse_a_completed_stage(client, database_path, stage, decision):
    case = make_case(client, database_path, 'ocr_done' if stage == 1 else 'stage1_approved')
    assert review(client, case['id'], stage, decision).status_code == 200
    assert review(client, case['id'], stage, 'approve').status_code == 409
    assert review(client, case['id'], stage, 'reject').status_code == 409
    assert len(stored_reviews(database_path, case['id'])) == 1


@pytest.mark.parametrize('body', [
    {}, {'stage': 1}, {'decision': 'approve'}, {'stage': 3, 'decision': 'approve'},
    {'stage': 0, 'decision': 'approve'}, {'stage': True, 'decision': 'approve'},
    {'stage': '1', 'decision': 'approve'}, {'stage': 1.0, 'decision': 'approve'},
    {'stage': 1, 'decision': 'APPROVE'}, {'stage': 1, 'decision': None},
    {'stage': 1, 'decision': 'approve', 'status': 'stage2_approved'},
])
def test_invalid_review_payload_is_422_and_never_changes_case(client, database_path, body):
    case = make_case(client, database_path)
    response = client.post(f"/api/cases/{case['id']}/review", json=body)
    assert response.status_code == 422
    current = client.get(f"/api/batches/{case['batch_id']}/cases").json()['items'][0]
    assert current['status'] == 'ocr_done'
    assert stored_reviews(database_path, case['id']) == []


def test_unknown_case_is_404(client):
    response = review(client, 'missing')
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'case_not_found'


@pytest.mark.parametrize('stage', [1, 2])
@pytest.mark.parametrize('decisions', [('approve', 'approve'), ('approve', 'reject')])
def test_concurrent_reviews_have_one_winner_and_one_audit_record(client, database_path, stage, decisions):
    case = make_case(client, database_path, 'ocr_done' if stage == 1 else 'stage1_approved')
    barrier = Barrier(2)

    def attempt(decision):
        barrier.wait(timeout=5)
        return review(client, case['id'], stage, decision)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(attempt, decisions))
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(r.json() for r in responses if r.status_code == 200)
    loser = next(r.json() for r in responses if r.status_code == 409)
    assert loser['detail']['current_status'] == winner['status']
    records = stored_reviews(database_path, case['id'])
    assert len(records) == 1
    assert records[0]['decision'] == winner['review']['decision']
    assert records[0]['new_status'] == winner['status']


def test_audit_insert_failure_rolls_back_case_status(client, database_path):
    case = make_case(client, database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute('''CREATE TRIGGER fail_review BEFORE INSERT ON reviews
            BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END''')
    response = review(client, case['id'])
    assert response.status_code == 500
    assert 'injected audit failure' not in response.text
    current = client.get(f"/api/batches/{case['batch_id']}/cases").json()['items'][0]
    assert current['status'] == 'ocr_done'
    assert stored_reviews(database_path, case['id']) == []


@pytest.mark.parametrize('body', [
    b'{"stage":NaN,"decision":"approve"}',
    b'{"stage":Infinity,"decision":"approve"}',
    b'{"stage":1,"decision":"\\ud800"}',
    b'{"stage":1,"decision":"approve","\\ud800":1}',
], ids=['nan', 'infinity', 'surrogate-value', 'surrogate-field'])
def test_unserializable_invalid_input_still_returns_safe_422(client, database_path, body):
    case = make_case(client, database_path)
    response = client.post(f"/api/cases/{case['id']}/review", content=body,
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert response.json()['detail']
    for error in response.json()['detail']:
        assert set(error) == {'type', 'loc', 'msg'}
        for part in error['loc']:
            if isinstance(part, str):
                part.encode('utf-8')  # Even the field location must be valid Unicode.
    assert stored_reviews(database_path, case['id']) == []
    assert client.get(f"/api/batches/{case['batch_id']}/cases").json()['items'][0]['status'] == 'ocr_done'
