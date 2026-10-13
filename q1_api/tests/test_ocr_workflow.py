import asyncio
import sqlite3
from threading import Event
import time
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ocr import OCRManager
from app.ocr_client import OCRSettings
import httpx

from conftest import application, csv_bytes, upload
from test_review import make_case, review, stored_reviews


FIELDS = {'id_number': 'TEST-ID-654321', 'full_name': 'OCR Name', 'dob': '1990-01-01',
          'issue_date': '2020-01-01', 'confidence': 0.91}


@pytest.fixture(autouse=True)
def fast_ocr(monkeypatch):
    monkeypatch.setenv('OCR_MAX_ATTEMPTS', '3')
    monkeypatch.setenv('OCR_BACKOFF_SECONDS', '0')


def outcome(fields=None, code=None, reason=None, retryable=False):
    return SimpleNamespace(fields=fields, code=code, reason=reason, retryable=retryable)


def setup_parser(client, monkeypatch, parser):
    monkeypatch.setattr(client.app.state.ocr.client, 'parse', parser)


def finished(client, batch_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        batch = client.get(f'/api/batches/{batch_id}').json()
        if batch.get('ocr_run') and batch['ocr_run']['status'] != 'running':
            return batch
        time.sleep(0.01)
    raise AssertionError('OCR did not finish within test deadline')


def cases(client, batch_id):
    return client.get(f'/api/batches/{batch_id}/cases?page_size=100').json()['items']


def test_process_endpoint_exists_and_unknown_batch_is_404(client):
    response = client.post('/api/batches/missing/process')
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'batch_not_found'


def test_process_returns_before_ocr_and_repeated_trigger_reuses_run(client, monkeypatch):
    batch = upload(client, csv_bytes(application(), application('A-2'))).json()
    release = Event()
    entered = Event()
    calls = []

    async def parse(case_id, image_ref):
        calls.append(case_id)
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return outcome(fields=FIELDS)

    setup_parser(client, monkeypatch, parse)
    try:
        response = client.post(f"/api/batches/{batch['id']}/process")
        assert response.status_code == 202
        assert entered.wait(2)
        assert response.json()['ocr_run']['status'] == 'running'
        again = client.post(f"/api/batches/{batch['id']}/process")
        assert again.json()['ocr_run']['id'] == response.json()['ocr_run']['id']
        assert client.get(f"/api/batches/{batch['id']}").json()['status_counts']['pending'] == 2
        assert len(calls) == 1
    finally:
        release.set()
    result = finished(client, batch['id'])
    assert result['status_counts']['ocr_done'] == 2
    assert result['ocr_run']['completed'] == 2
    assert len(calls) == 2
    for case in cases(client, batch['id']):
        assert case['ocr_fields'] == FIELDS
        assert case['applicant_name'] == 'Test Applicant'
        assert case['id_number'] == 'TEST-ID-123'
        assert case['ocr_attempts'] == 1


@pytest.mark.parametrize('code,retryable,want_calls', [
    ('http_422', False, 1), ('http_400', False, 1), ('invalid_response', False, 1),
    ('http_500', True, 3), ('timeout', True, 3), ('network_error', True, 3),
])
def test_failures_have_bounded_attempts_visible_reason_and_recovery(client, monkeypatch, database_path,
                                                                  code, retryable, want_calls):
    case = make_case(client, database_path, 'pending')
    calls = []

    async def parse(case_id, image_ref):
        calls.append(case_id)
        return outcome(code=code, reason='controlled service reason', retryable=retryable)

    setup_parser(client, monkeypatch, parse)
    assert client.post(f"/api/batches/{case['batch_id']}/process").status_code == 202
    finished(client, case['batch_id'])
    current = cases(client, case['batch_id'])[0]
    assert current['status'] == 'ocr_failed'
    assert current['ocr_error'] == 'controlled service reason'
    assert current['ocr_error_code'] == code
    assert current['ocr_retryable'] is retryable
    assert current['ocr_attempts'] == want_calls == len(calls)

    async def recover(case_id, image_ref):
        calls.append(case_id)
        return outcome(fields=FIELDS)

    setup_parser(client, monkeypatch, recover)
    assert client.post(f"/api/batches/{case['batch_id']}/process").status_code == 202
    finished(client, case['batch_id'])
    current = cases(client, case['batch_id'])[0]
    assert current['status'] == ('ocr_done' if retryable else 'ocr_failed')
    assert current['ocr_attempts'] == want_calls + int(retryable)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM ocr_attempts WHERE case_id=?',
                                  (case['id'],)).fetchone()[0] == want_calls + int(retryable)


def test_transient_errors_then_success_and_progress_is_visible(client, monkeypatch, database_path):
    batch = upload(client, csv_bytes(application(), application('A-2'))).json()
    release = Event()
    second = Event()
    attempts = {}

    async def parse(case_id, image_ref):
        attempts[case_id] = attempts.get(case_id, 0) + 1
        if len(attempts) == 2:
            second.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        if attempts[case_id] == 1:
            return outcome(code='http_500', reason='temporary', retryable=True)
        return outcome(fields=FIELDS)

    setup_parser(client, monkeypatch, parse)
    try:
        assert client.post(f"/api/batches/{batch['id']}/process").status_code == 202
        assert second.wait(2)
        counts = client.get(f"/api/batches/{batch['id']}").json()['status_counts']
        assert (counts['ocr_done'], counts['pending']) == (1, 1)
    finally:
        release.set()
    assert finished(client, batch['id'])['status_counts']['ocr_done'] == 2
    assert [c['ocr_attempts'] for c in cases(client, batch['id'])] == [2, 2]


@pytest.mark.parametrize('state', ['ocr_done', 'stage1_approved', 'stage1_rejected',
                                 'stage2_approved', 'stage2_rejected', 'exported'])
def test_process_never_restarts_successful_or_reviewed_cases(client, monkeypatch, database_path, state):
    case = make_case(client, database_path, state)

    async def forbidden(*args):
        pytest.fail('OCR must not be called for a completed/reviewed case')

    setup_parser(client, monkeypatch, forbidden)
    assert client.post(f"/api/batches/{case['batch_id']}/process").status_code == 202
    finished(client, case['batch_id'])
    assert cases(client, case['batch_id'])[0]['status'] == state


@pytest.mark.parametrize('late_success', [True, False])
def test_late_ocr_result_cannot_overwrite_real_two_stage_approval(client, monkeypatch, database_path, late_success):
    case = make_case(client, database_path, 'pending')
    release = Event()
    entered = Event()

    async def parse(*args):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return outcome(fields=FIELDS) if late_success else outcome(code='http_422', reason='late failure')

    setup_parser(client, monkeypatch, parse)
    try:
        assert client.post(f"/api/batches/{case['batch_id']}/process").status_code == 202
        assert entered.wait(2)
        # Simulate another worker committing OCR while this response remains in flight.
        with sqlite3.connect(database_path) as connection:
            connection.execute("UPDATE cases SET status='ocr_done' WHERE id=?", (case['id'],))
        assert review(client, case['id'], 1).status_code == 200
        assert review(client, case['id'], 2).status_code == 200
    finally:
        release.set()
    finished(client, case['batch_id'])
    current = cases(client, case['batch_id'])[0]
    assert current['status'] == 'stage2_approved'
    assert current['ocr_fields'] is None
    assert current['ocr_error'] is None
    assert len(stored_reviews(database_path, case['id'])) == 2


def test_unexpected_task_error_is_visible_and_retryable(client, monkeypatch, database_path):
    case = make_case(client, database_path, 'pending')

    async def broken(*args):
        raise RuntimeError('injected task fault with internal detail')

    setup_parser(client, monkeypatch, broken)
    assert client.post(f"/api/batches/{case['batch_id']}/process").status_code == 202
    batch = finished(client, case['batch_id'])
    assert batch['ocr_run']['status'] == 'failed'
    assert batch['ocr_run']['error']
    assert 'internal detail' not in batch['ocr_run']['error']
    current = cases(client, case['batch_id'])[0]
    assert current['status'] == 'ocr_failed'
    assert current['ocr_retryable'] is True
    assert current['ocr_error_code'] == 'task_error'


def test_old_generation_result_is_fenced_even_if_case_status_is_unchanged(client, database_path):
    store = client.app.state.ocr.store
    case = make_case(client, database_path, 'pending')
    old_id, old_cases = store.start(case['batch_id'])
    new_id, new_cases = store.start(case['batch_id'])
    assert not store.record(old_id, old_cases[0], 1, outcome(fields=FIELDS), True)
    assert cases(client, case['batch_id'])[0]['status'] == 'pending'
    store.finish(old_id, 'late old task failure')
    assert cases(client, case['batch_id'])[0]['status'] == 'pending'
    assert store.record(new_id, new_cases[0], 1, outcome(fields=FIELDS), True)
    store.finish(new_id)
    assert cases(client, case['batch_id'])[0]['status'] == 'ocr_done'
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT applied FROM ocr_attempts ORDER BY id').fetchall() == [(0,), (1,)]


def test_ocr_attempt_and_case_update_are_atomic(client, database_path):
    store = client.app.state.ocr.store
    case = make_case(client, database_path, 'pending')
    run_id, selected = store.start(case['batch_id'])
    with sqlite3.connect(database_path) as connection:
        connection.execute('''CREATE TRIGGER fail_ocr_update BEFORE UPDATE OF ocr_attempts ON cases
            BEGIN SELECT RAISE(ABORT, 'injected OCR update fault'); END''')
    with pytest.raises(sqlite3.IntegrityError):
        store.record(run_id, selected[0], 1, outcome(fields=FIELDS), True)
    assert cases(client, case['batch_id'])[0]['status'] == 'pending'
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM ocr_attempts').fetchone()[0] == 0


def test_concurrent_process_requests_share_one_durable_run(client, monkeypatch, database_path):
    case = make_case(client, database_path, 'pending')
    release = Event()
    calls = []
    barrier = Barrier(8)

    async def parse(*args):
        calls.append(args)
        while not release.is_set():
            await asyncio.sleep(0.01)
        return outcome(fields=FIELDS)

    setup_parser(client, monkeypatch, parse)
    def trigger(_):
        barrier.wait(timeout=5)
        return client.post(f"/api/batches/{case['batch_id']}/process")
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(trigger, range(8)))
        assert all(r.status_code == 202 for r in responses)
        assert len({r.json()['ocr_run']['id'] for r in responses}) == 1
        assert len(calls) == 1
    finally:
        release.set()
    finished(client, case['batch_id'])


def test_queue_backpressure_keeps_accepted_jobs_and_limits_global_concurrency(client, monkeypatch):
    batches = [upload(client, csv_bytes(application())).json() for _ in range(5)]
    release = Event()
    entered = Event()
    calls = []

    async def parse(*args):
        calls.append(args)
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return outcome(fields=FIELDS)

    setup_parser(client, monkeypatch, parse)
    try:
        for batch in batches[:4]:
            assert client.post(f"/api/batches/{batch['id']}/process").status_code == 202
        assert entered.wait(2)
        rejected = client.post(f"/api/batches/{batches[4]['id']}/process")
        assert rejected.status_code == 429
        assert rejected.headers['Retry-After'] == '1'
        assert len(calls) == 1
        assert client.get(f"/api/batches/{batches[4]['id']}").json()['ocr_run'] is None
    finally:
        release.set()
    for batch in batches[:4]:
        assert finished(client, batch['id'])['status_counts']['ocr_done'] == 1


def test_lifespan_shutdown_cancels_inflight_request_and_normal_process_recovers(database_path, monkeypatch):
    entered, cancelled = Event(), Event()
    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    with TestClient(app) as client:
        case = make_case(client, database_path, 'pending')
        setup_parser(client, monkeypatch, blocked)
        assert client.post(f"/api/batches/{case['batch_id']}/process").status_code == 202
        assert entered.wait(2)
    assert cancelled.is_set()
    with TestClient(app) as client:
        batch = client.get(f"/api/batches/{case['batch_id']}").json()
        assert batch['ocr_run']['status'] == 'interrupted'
        async def success(*args):
            return outcome(fields=FIELDS)
        setup_parser(client, monkeypatch, success)
        assert client.post(f"/api/batches/{case['batch_id']}/process").status_code == 202
        assert finished(client, case['batch_id'])['status_counts']['ocr_done'] == 1


def test_storage_unavailable_during_task_failure_is_reconciled_when_database_recovers(client, monkeypatch,
                                                                                     database_path):
    case = make_case(client, database_path, 'pending')
    entered, release = Event(), Event()
    async def fail(*args):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        raise RuntimeError('controlled failure')
    setup_parser(client, monkeypatch, fail)
    assert client.post(f"/api/batches/{case['batch_id']}/process").status_code == 202
    assert entered.wait(2)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute('BEGIN IMMEDIATE')
        release.set()
        deadline = time.monotonic() + 4
        while client.app.state.ocr.tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not client.app.state.ocr.tasks
    finally:
        connection.rollback()
        connection.close()
    batch = client.get(f"/api/batches/{case['batch_id']}").json()
    assert batch['ocr_run']['status'] == 'failed'
    assert batch['ocr_run']['error']
    current = cases(client, case['batch_id'])[0]
    assert current['status'] == 'ocr_failed'
    assert current['ocr_retryable'] is True


def test_cancelled_database_thread_cannot_write_over_replacement_run(client, monkeypatch, database_path):
    case = make_case(client, database_path, 'pending')
    manager = client.app.state.ocr
    original_record = manager.store.record
    entered, release, old_done = Event(), Event(), Event()
    old_run = []

    def paused_record(run_id, *args):
        if not old_run:
            old_run.append(run_id)
            entered.set()
            assert release.wait(5)
            try:
                return original_record(run_id, *args)
            finally:
                old_done.set()
        return original_record(run_id, *args)

    async def success(*args):
        return outcome(fields=FIELDS)

    setup_parser(client, monkeypatch, success)
    monkeypatch.setattr(manager.store, 'record', paused_record)
    try:
        assert client.post(f"/api/batches/{case['batch_id']}/process").status_code == 202
        assert entered.wait(2)
        client.portal.call(manager.close)
        newer = {**FIELDS, 'full_name': 'Newer OCR result'}
        async def replacement(*args):
            return outcome(fields=newer)
        setup_parser(client, monkeypatch, replacement)
        assert client.post(f"/api/batches/{case['batch_id']}/process").status_code == 202
        finished(client, case['batch_id'])
    finally:
        release.set()
    assert old_done.wait(2)
    current = cases(client, case['batch_id'])[0]
    assert current['status'] == 'ocr_done'
    assert current['ocr_fields'] == newer
    assert current['ocr_attempts'] == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT applied FROM ocr_attempts WHERE run_id=?',
                                  (old_run[0],)).fetchone()[0] == 0


def test_additive_q1_migration_is_repeatable_and_preserves_reviews(client, database_path):
    case = make_case(client, database_path)
    assert review(client, case['id'], 1).status_code == 200
    assert review(client, case['id'], 2).status_code == 200
    before = cases(client, case['batch_id'])
    history = stored_reviews(database_path, case['id'])
    with sqlite3.connect(database_path) as connection:
        connection.execute('DROP TABLE ocr_attempts')
        connection.execute('DROP TABLE ocr_runs')
        for column in ['ocr_fields', 'ocr_error', 'ocr_error_code', 'ocr_retryable', 'ocr_attempts', 'ocr_token']:
            connection.execute(f'ALTER TABLE cases DROP COLUMN {column}')
        # The remaining names are the original Q1 schema, with its constraints intact.
        assert [r[1] for r in connection.execute('PRAGMA table_info(cases)')] == [
            'id', 'batch_id', 'row_number', 'case_ref', 'applicant_name', 'id_number',
            'dob', 'issue_date', 'image_ref', 'status']
    client.app.state.database.initialize()
    client.app.state.database.initialize()
    assert cases(client, case['batch_id']) == before
    assert stored_reviews(database_path, case['id']) == history


def test_all_rejected_batch_finishes_empty_without_ocr_calls(client, monkeypatch):
    batch = upload(client, csv_bytes(application(applicant_name=''))).json()
    assert batch['accepted_count'] == 0
    async def forbidden(*args):
        pytest.fail('An empty batch must not call OCR')
    setup_parser(client, monkeypatch, forbidden)
    response = client.post(f"/api/batches/{batch['id']}/process")
    assert response.status_code == 202
    assert response.json()['ocr_run']['status'] == 'completed'
    assert response.json()['ocr_run']['total'] == response.json()['ocr_run']['completed'] == 0


async def test_cancelled_claim_does_not_leave_a_running_job_without_a_task(client, monkeypatch, database_path):
    case = make_case(client, database_path, 'pending')
    entered, release, done = Event(), Event(), Event()
    async with httpx.AsyncClient() as http_client:
        manager = OCRManager(client.app.state.database, OCRSettings(), http_client)
        original_start = manager.store.start
        def slow_claim(batch_id):
            entered.set()
            assert release.wait(5)
            try:
                return original_start(batch_id)
            finally:
                done.set()
        monkeypatch.setattr(manager.store, 'start', slow_claim)
        task = asyncio.create_task(manager.start(case['batch_id']))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await asyncio.to_thread(done.wait, 2)
            batch = client.get(f"/api/batches/{case['batch_id']}").json()
            assert batch['ocr_run']['status'] == 'interrupted'
            assert not manager.tasks
            assert cases(client, case['batch_id'])[0]['status'] == 'pending'
        finally:
            release.set()
            await manager.close()


def test_exponential_backoff_is_observed_and_capped(client, monkeypatch, database_path):
    case = make_case(client, database_path, 'pending')
    manager = client.app.state.ocr
    manager.settings = OCRSettings(max_attempts=4, backoff_seconds=0.04, backoff_cap_seconds=0.06)
    starts = []
    async def parse(*args):
        starts.append(time.monotonic())
        return outcome(fields=FIELDS) if len(starts) == 4 else outcome(code='http_500', reason='temporary', retryable=True)
    setup_parser(client, monkeypatch, parse)
    assert client.post(f"/api/batches/{case['batch_id']}/process").status_code == 202
    assert finished(client, case['batch_id'])['status_counts']['ocr_done'] == 1
    gaps = [end - start for start, end in zip(starts, starts[1:])]
    assert len(gaps) == 3
    assert all(gap >= minimum for gap, minimum in zip(gaps, [0.035, 0.055, 0.055]))
    assert max(gaps) < 1  # Allows loaded CI overhead while rejecting unbounded waits.
