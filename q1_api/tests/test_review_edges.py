"""Approval boundary/fault drills using real HTTP handlers and SQLite transactions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os
import socket
import sqlite3
import subprocess
import sys
from threading import Barrier
import time
from urllib.parse import quote

import pytest

from test_native_process import ROOT, api_process
from test_review import make_case, review, stored_reviews


def case_status(client, case):
    items = client.get(f"/api/batches/{case['batch_id']}/cases").json()['items']
    return next(item['status'] for item in items if item['id'] == case['id'])


@pytest.mark.parametrize('body', [
    b'', b'null', b'[]', b'1', b'"approve"', b'{"stage":1,',
    b'{"stage":null,"decision":"approve"}',
    b'{"stage":-1,"decision":"approve"}',
    b'{"stage":[],"decision":"approve"}',
    b'{"stage":{},"decision":"approve"}',
    b'{"stage":1,"decision":true}',
    b'{"stage":1,"decision":[]}',
    b'{"stage":1,"decision":{}}',
    b'{"stage":1,"decision":" approve "}',
    b'{"stage":1,"decision":""}',
    b'{"stage":1,"decision":"approve\\u0000"}',
], ids=['empty', 'null', 'array', 'number', 'string', 'broken-json', 'null-stage',
        'negative-stage', 'array-stage', 'object-stage', 'boolean-decision',
        'array-decision', 'object-decision', 'padded-decision', 'empty-decision', 'nul-decision'])
def test_malformed_review_never_changes_state_or_history(client, database_path, body):
    # Catches weakened request validation or writes before validation completes.
    case = make_case(client, database_path)
    response = client.post(f"/api/cases/{case['id']}/review", content=body,
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert response.json()['detail']
    assert case_status(client, case) == 'ocr_done'
    assert stored_reviews(database_path, case['id']) == []


@pytest.mark.parametrize('body', [
    b'{"stage":1,"decision":"reject","decision":"approve"}',
    b'{"stage":1,"decision":"approve","decision":"reject"}',
    b'{"stage":2,"stage":1,"decision":"approve"}',
    b'{"stage":1,"stage":1,"decision":"approve"}',
    b'{"stage":1,"decision":"approve","decision":"approve"}',
    b'{"stage":1,"decision":"reject","deci\\u0073ion":"approve"}',
], ids=['reject-then-approve', 'approve-then-reject', 'duplicate-stage',
        'same-stage', 'same-decision', 'escaped-duplicate'])
def test_duplicate_json_fields_are_rejected_without_choosing_a_decision(client, database_path, body):
    case = make_case(client, database_path)
    response = client.post(f"/api/cases/{case['id']}/review", content=body,
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'duplicate_json_field'
    assert case_status(client, case) == 'ocr_done'
    assert stored_reviews(database_path, case['id']) == []


@pytest.mark.parametrize('body,content_type,expected', [
    (b'\xff', 'application/json', 400),
    (b'{"stage":1,"decision":"approve"}', 'text/plain', 422),
])
def test_invalid_body_encoding_or_media_is_safe(client, database_path, body, content_type, expected):
    case = make_case(client, database_path)
    response = client.post(f"/api/cases/{case['id']}/review", content=body,
                           headers={'Content-Type': content_type})
    assert response.status_code == expected
    assert case_status(client, case) == 'ocr_done'
    assert stored_reviews(database_path, case['id']) == []


@pytest.mark.parametrize('identifier', ['A-1', '00000000-0000-0000-0000-000000000000',
                                       "x' OR 1=1 --", '\x00'])
def test_unknown_or_hostile_identifier_cannot_target_another_case(client, database_path, identifier):
    case = make_case(client, database_path)
    response = review(client, quote(identifier, safe=''))
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'case_not_found'
    assert case_status(client, case) == 'ocr_done'
    assert stored_reviews(database_path, case['id']) == []


def test_query_parameters_cannot_override_review_body(client, database_path):
    case = make_case(client, database_path)
    response = client.post(f"/api/cases/{case['id']}/review?stage=1&decision=approve",
                           json={'stage': 2, 'decision': 'approve'})
    assert response.status_code == 409
    assert case_status(client, case) == 'ocr_done'
    assert stored_reviews(database_path, case['id']) == []


@pytest.mark.parametrize('stage', [1, 2])
@pytest.mark.parametrize('decisions', [('approve',) * 8, ('approve', 'reject') * 4],
                         ids=['eight-approvals', 'eight-conflicting-decisions'])
def test_eight_concurrent_requests_commit_only_one_decision(client, database_path, stage, decisions):
    case = make_case(client, database_path, 'ocr_done' if stage == 1 else 'stage1_approved')
    barrier = Barrier(len(decisions))

    def attempt(decision):
        barrier.wait(timeout=10)
        return review(client, case['id'], stage, decision)

    with ThreadPoolExecutor(max_workers=len(decisions)) as pool:
        responses = list(pool.map(attempt, decisions))
    assert sorted(r.status_code for r in responses) == [200] + [409] * 7
    winner = next(r.json() for r in responses if r.status_code == 200)
    history = stored_reviews(database_path, case['id'])
    assert len(history) == 1
    assert history[0]['decision'] == winner['review']['decision']
    assert history[0]['new_status'] == case_status(client, case) == winner['status']


@pytest.mark.parametrize('first', ['approve', 'reject'])
@pytest.mark.parametrize('second', ['approve', 'reject'])
def test_concurrent_different_stages_follow_commit_order(client, database_path, first, second):
    # Two simultaneous stages may both succeed, but only with a committed stage 1 approval first.
    case = make_case(client, database_path)
    barrier = Barrier(2)

    def attempt(command):
        barrier.wait(timeout=10)
        return review(client, case['id'], *command)

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = list(pool.map(attempt, [(1, first), (2, second)]))
    assert one.status_code == 200
    history = stored_reviews(database_path, case['id'])
    assert history[0]['stage'] == 1
    if first == 'reject':
        assert two.status_code == 409
        assert len(history) == 1
        assert case_status(client, case) == 'stage1_rejected'
    elif two.status_code == 200:
        assert [(r['stage'], r['previous_status']) for r in history] == [
            (1, 'ocr_done'), (2, 'stage1_approved')]
        assert case_status(client, case) == ('stage2_approved' if second == 'approve' else 'stage2_rejected')
    else:
        assert two.status_code == 409
        assert len(history) == 1
        assert case_status(client, case) == 'stage1_approved'
        # Stage 2 arrived too early; after reloading it can be submitted successfully.
        assert review(client, case['id'], 2, second).status_code == 200


def test_same_reference_in_different_batches_has_independent_reviews(client, database_path):
    cases = [make_case(client, database_path) for _ in range(2)]
    assert cases[0]['case_ref'] == cases[1]['case_ref']
    barrier = Barrier(2)

    def attempt(item):
        case, decision = item
        barrier.wait(timeout=10)
        return review(client, case['id'], 1, decision)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(attempt, zip(cases, ['approve', 'reject'])))
    assert [r.status_code for r in responses] == [200, 200]
    for case, state in zip(cases, ['stage1_approved', 'stage1_rejected']):
        assert case_status(client, case) == state
        assert len(stored_reviews(database_path, case['id'])) == 1
        batch = client.get(f"/api/batches/{case['batch_id']}").json()
        assert batch['status_counts'][state] == 1
        assert batch['status_counts']['ocr_done'] == 0
        assert sum(batch['status_counts'].values()) == batch['accepted_count'] == 1


@pytest.mark.parametrize('lock', ['writer', 'reader-blocking-commit'])
def test_storage_lock_returns_retryable_503_and_rolls_back(client, database_path, lock):
    case = make_case(client, database_path)
    connection = sqlite3.connect(database_path)
    try:
        if lock == 'writer':
            connection.execute('BEGIN IMMEDIATE')
        else:
            # A real shared read lock lets UPDATE/INSERT run, but prevents their COMMIT.
            assert connection.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
            connection.execute('BEGIN')
            connection.execute('SELECT * FROM cases').fetchall()
        response = review(client, case['id'])
        assert response.status_code == 503
        assert response.headers['Retry-After'] == '1'
        assert response.json()['detail']['code'] == 'storage_unavailable'
    finally:
        connection.rollback()
        connection.close()
    assert case_status(client, case) == 'ocr_done'
    assert stored_reviews(database_path, case['id']) == []
    assert review(client, case['id']).status_code == 200
    assert len(stored_reviews(database_path, case['id'])) == 1


def test_case_update_failure_writes_no_history_and_can_retry(client, database_path):
    case = make_case(client, database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute('''CREATE TRIGGER fail_update BEFORE UPDATE OF status ON cases
            BEGIN SELECT RAISE(ABORT, 'injected update failure'); END''')
    response = review(client, case['id'])
    assert response.status_code == 500
    assert 'injected update failure' not in response.text
    assert case_status(client, case) == 'ocr_done'
    assert stored_reviews(database_path, case['id']) == []
    with sqlite3.connect(database_path) as connection:
        connection.execute('DROP TRIGGER fail_update')
    assert review(client, case['id']).status_code == 200
    assert len(stored_reviews(database_path, case['id'])) == 1


@pytest.mark.parametrize('cut', ['before-commit', 'after-commit'])
def test_killed_writer_leaves_atomic_state_and_safe_retry(client, database_path, tmp_path, cut):
    # Kill a real process at a known transaction boundary; the API/database stay unmodified.
    case = make_case(client, database_path)
    ready = tmp_path / 'writer-ready'
    script = '''
from contextlib import contextmanager
from pathlib import Path
import signal, sys
from app.db import Database
path, case_id, cut, marker = sys.argv[1:]
db = Database(Path(path))
def pause():
    Path(marker).write_text(cut)
    signal.pause()
if cut == 'before-commit':
    original = db.connect
    @contextmanager
    def connection_with_cutpoint():
        with original() as connection:
            connection.create_function('pause_at_cutpoint', 0, pause)
            connection.execute("CREATE TEMP TRIGGER cutpoint AFTER INSERT ON reviews "
                               "BEGIN SELECT pause_at_cutpoint(); END")
            yield connection
    db.connect = connection_with_cutpoint
db.review_case(case_id, 1, 'approve')
pause()
'''
    environment = {**os.environ, 'PYTHONPATH': str(ROOT / 'q1_api')}
    with (tmp_path / 'writer.log').open('w+') as log:
        process = subprocess.Popen([sys.executable, '-c', script, str(database_path),
                                    case['id'], cut, str(ready)], env=environment,
                                   stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                assert process.poll() is None, 'Writer exited before the requested cutpoint'
                time.sleep(0.01)
            assert ready.exists(), 'Writer never reached the requested cutpoint'
        finally:
            process.kill()
            process.wait(timeout=5)
    before = cut == 'before-commit'
    assert case_status(client, case) == ('ocr_done' if before else 'stage1_approved')
    assert len(stored_reviews(database_path, case['id'])) == (0 if before else 1)
    assert review(client, case['id']).status_code == (200 if before else 409)
    assert len(stored_reviews(database_path, case['id'])) == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('second_decision', ['approve', 'reject'])
def test_two_real_api_processes_cannot_both_decide(client, database_path, tmp_path, second_decision):
    case = make_case(client, database_path)
    with ExitStack() as stack:
        clients = []
        for index in range(2):
            listener = stack.enter_context(socket.socket())
            listener.bind(('127.0.0.1', 0))
            clients.append(stack.enter_context(api_process(listener, database_path,
                                                           tmp_path / f'api-{index}.log')))
        barrier = Barrier(2)

        def attempt(command):
            remote, decision = command
            barrier.wait(timeout=10)
            return review(remote, case['id'], decision=decision)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(attempt, zip(clients, ['approve', second_decision])))
        assert sorted(r.status_code for r in responses) == [200, 409]
        winner = next(r.json() for r in responses if r.status_code == 200)
    assert case_status(client, case) == winner['status']
    records = stored_reviews(database_path, case['id'])
    assert len(records) == 1
    assert records[0]['decision'] == winner['review']['decision']
