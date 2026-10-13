"""Delivery behavior through the API and durable fencing boundaries."""
import asyncio
import hashlib
import time

import pytest

from app.delivery import DeliveryManager
from app.delivery_client import DeliveryResult
from app.exports import ExportStore
from app.main import app
from test_exports import prepared


def receipt(export):
    return {'delivery_id': 'DLV-1', 'idempotency_key': export['idempotency_key'],
            'file_name': export['file_name'], 'sha256': export['sha256'],
            'ingested_at': 1, 'attempts': 1}


def finished(client, export_id, expected='delivered'):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        response = client.get('/api/exports/' + export_id)
        assert response.status_code == 200
        result = response.json()
        if result['status'] == expected:
            return result
        time.sleep(.01)
    pytest.fail(f'Export did not become {expected}: {result}')


def test_export_api_download_receipt_and_delivered_replay(client, database_path, monkeypatch):
    store, batch_id = prepared(client, database_path)
    calls = []
    async def send(export):
        calls.append(export)
        return DeliveryResult(receipt=receipt(export))
    monkeypatch.setattr(app.state.delivery.client, 'send', send)
    response = client.post(f'/api/batches/{batch_id}/export')
    assert response.status_code == 202
    export_id = response.json()['export']['id']
    delivered = finished(client, export_id)
    assert delivered['receipt'] == receipt(calls[0])
    assert delivered['attempts'] == 1
    assert 'content' not in delivered and 'delivery_token' not in delivered
    download = client.get(f'/api/exports/{export_id}/download')
    assert download.status_code == 200
    assert download.headers['content-type'].startswith('text/csv')
    assert delivered['file_name'] in download.headers['content-disposition']
    assert hashlib.sha256(download.content).hexdigest() == delivered['sha256']
    assert download.content == calls[0]['content']
    assert client.post(f'/api/exports/{export_id}/deliver').json()['receipt'] == delivered['receipt']
    assert client.post(f'/api/batches/{batch_id}/export').json() == {'count': 0, 'export': None}
    assert [item['id'] for item in client.get(f'/api/batches/{batch_id}/exports').json()] == [export_id]
    assert len(calls) == 1


def test_transient_failures_are_bounded_and_manual_retry_reuses_bytes(client, database_path, monkeypatch):
    _, batch_id = prepared(client, database_path)
    calls = []
    async def send(export):
        calls.append((export['idempotency_key'], export['content']))
        return DeliveryResult(code='timeout', reason='Response timed out.', retryable=True)
    monkeypatch.setattr(app.state.delivery.client, 'send', send)
    export_id = client.post(f'/api/batches/{batch_id}/export').json()['export']['id']
    failed = finished(client, export_id, 'failed')
    assert failed['attempts'] == app.state.delivery.settings.max_attempts == 4
    assert failed['retryable'] and failed['receipt'] is None
    assert len(set(calls)) == 1
    async def succeed(export):
        calls.append((export['idempotency_key'], export['content']))
        return DeliveryResult(receipt=receipt(export))
    monkeypatch.setattr(app.state.delivery.client, 'send', succeed)
    assert client.post(f'/api/exports/{export_id}/deliver').status_code == 202
    done = finished(client, export_id)
    assert done['attempts'] == 5
    assert done['error'] is None and not done['retryable']
    assert len(set(calls)) == 1
    assert client.get(f'/api/batches/{batch_id}').json()['status_counts']['exported'] == 1


@pytest.mark.parametrize('code', ['http_400', 'receipt_mismatch', 'invalid_receipt'])
def test_permanent_failure_stops_after_one_attempt(client, database_path, monkeypatch, code):
    _, batch_id = prepared(client, database_path)
    async def send(export):
        return DeliveryResult(code=code, reason='Cannot accept receipt.')
    monkeypatch.setattr(app.state.delivery.client, 'send', send)
    export_id = client.post(f'/api/batches/{batch_id}/export').json()['export']['id']
    failed = finished(client, export_id, 'failed')
    assert failed['attempts'] == 1 and not failed['retryable']
    assert failed['error_code'] == code
    assert client.get(f'/api/batches/{batch_id}').json()['status_counts']['exported'] == 1


def test_task_exception_is_queryable_and_recoverable(client, database_path, monkeypatch):
    _, batch_id = prepared(client, database_path)
    async def send(export):
        raise RuntimeError('injected worker failure')
    monkeypatch.setattr(app.state.delivery.client, 'send', send)
    export_id = client.post(f'/api/batches/{batch_id}/export').json()['export']['id']
    failed = finished(client, export_id, 'failed')
    assert failed['error_code'] == 'task_interrupted'
    assert failed['retryable'] and 'injected' not in failed['error']


def test_recovery_and_stale_write_cannot_overwrite_new_receipt(client, database_path, monkeypatch):
    # Stop automatic polling by using an independent, never started database.
    from app.db import Database
    from app.imports import parse_csv
    from conftest import application, csv_bytes
    import json
    from test_exports import OCR
    db = Database(database_path.parent / 'isolated.sqlite3')
    db.initialize()
    batch_id = db.import_batch(parse_csv(csv_bytes(application())))['id']
    with db.connect() as connection:
        connection.execute("UPDATE cases SET status='stage2_approved',ocr_fields=?", (json.dumps(OCR),))
    store = ExportStore(db)
    export = store.create(batch_id)['export']
    stale = store.claim()
    unknown_attempt = store.begin_attempt(stale)
    store.recover()
    current = store.claim()
    assert current['delivery_token'] != stale['delivery_token']
    assert current['content'] == stale['content'] == export['content']
    attempt = store.begin_attempt(current)
    assert store.record(current, attempt, DeliveryResult(receipt=receipt(current)), True)
    assert not store.record(stale, unknown_attempt, DeliveryResult(code='timeout', retryable=True), True)
    store.fail(stale)
    store.recover()
    store.retry(export['id'])
    result = store.get(export['id'])
    assert result['status'] == 'delivered' and result['receipt'] == receipt(current)
    assert result['attempts'] == 2
    assert store.claim() is None


def isolated_export(tmp_path):
    import json
    from app.db import Database
    from app.imports import parse_csv
    from conftest import application, csv_bytes
    from test_exports import OCR
    database = Database(tmp_path / 'delivery-only.sqlite3')
    database.initialize()
    batch_id = database.import_batch(parse_csv(csv_bytes(application())))['id']
    with database.connect() as connection:
        connection.execute("UPDATE cases SET status='stage2_approved',ocr_fields=?", (json.dumps(OCR),))
    export = ExportStore(database).create(batch_id)['export']
    return database, export


async def until(predicate, timeout=4):
    async def poll():
        while not predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(poll(), timeout)


async def test_cancel_during_committing_claim_is_not_orphaned(tmp_path, monkeypatch):
    from threading import Event
    import httpx
    from app.delivery_client import DeliverySettings
    database, export = isolated_export(tmp_path)
    entered, release = Event(), Event()
    async with httpx.AsyncClient() as http:
        manager = DeliveryManager(database, DeliverySettings(), http)
        original = manager.store.claim
        def blocked_claim():
            claimed = original()
            entered.set()
            assert release.wait(4)
            return claimed
        monkeypatch.setattr(manager.store, 'claim', blocked_claim)
        await manager.start()
        try:
            await until(entered.is_set)
            manager.task.cancel()
            await asyncio.sleep(.02)
            assert not manager.task.done()
        finally:
            release.set()
            await manager.close()
        result = manager.store.get(export['id'])
        assert result['status'] == 'failed'
        assert result['retryable'] and result['delivery_token'] is None
        assert result['attempts'] == 0
        assert manager.store.retry(export['id'])['content'] == export['content']


async def test_cancelled_record_thread_cannot_overwrite_retry_receipt(tmp_path, monkeypatch):
    from threading import Event
    import httpx
    from app.delivery_client import DeliverySettings
    database, export = isolated_export(tmp_path)
    entered, release, done = Event(), Event(), Event()
    async with httpx.AsyncClient() as http:
        manager = DeliveryManager(database, DeliverySettings(), http)
        original = manager.store.record
        def blocked_record(*args):
            entered.set()
            assert release.wait(4)
            try:
                return original(*args)
            finally:
                done.set()
        async def failure(_):
            return DeliveryResult(code='timeout', retryable=True)
        monkeypatch.setattr(manager.store, 'record', blocked_record)
        monkeypatch.setattr(manager.client, 'send', failure)
        await manager.start()
        try:
            await until(entered.is_set)
            await manager.close()
            manager.store.retry(export['id'])
            newer = manager.store.claim()
            attempt = manager.store.begin_attempt(newer)
            assert original(newer, attempt, DeliveryResult(receipt=receipt(newer)), True)
        finally:
            release.set()
        await until(done.is_set)
        result = manager.store.get(export['id'])
        assert result['status'] == 'delivered' and result['receipt'] == receipt(newer)
        with database.connect() as connection:
            assert [row['applied'] for row in connection.execute(
                'SELECT applied FROM delivery_attempts ORDER BY id')] == [0, 1]


async def test_database_outage_defers_failure_then_recovers_without_resending(tmp_path, monkeypatch):
    import sqlite3
    import httpx
    from app.delivery_client import DeliverySettings
    database, export = isolated_export(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async with httpx.AsyncClient() as http:
        manager = DeliveryManager(database, DeliverySettings(), http)
        async def failure(value):
            calls.append(value['id'])
            entered.set()
            await release.wait()
            raise RuntimeError('injected task failure while storage is locked')
        monkeypatch.setattr(manager.client, 'send', failure)
        await manager.start()
        await asyncio.wait_for(entered.wait(), 2)
        lock = database.connect()
        connection = lock.__enter__()
        try:
            connection.execute('BEGIN IMMEDIATE')
            release.set()
            await until(lambda: manager.deferred is not None)
        finally:
            connection.rollback()
            lock.__exit__(None, None, None)
        try:
            await until(lambda: manager.store.get(export['id'])['status'] == 'failed')
            assert calls == [export['id']]
            assert manager.store.get(export['id'])['retryable']
        finally:
            await manager.close()


async def test_receipt_write_failure_retries_same_remote_identity(tmp_path, monkeypatch):
    import sqlite3
    import httpx
    from app.delivery_client import DeliverySettings
    database, export = isolated_export(tmp_path)
    remote = {}
    async with httpx.AsyncClient() as http:
        manager = DeliveryManager(database, DeliverySettings(), http)
        original = manager.store.record
        failed = False
        def fail_once(*args):
            nonlocal failed
            if not failed:
                failed = True
                raise sqlite3.OperationalError('injected receipt persistence failure')
            return original(*args)
        async def ingest(value):
            remote.setdefault(value['idempotency_key'], []).append(value['content'])
            return DeliveryResult(receipt=receipt(value))
        monkeypatch.setattr(manager.client, 'send', ingest)
        monkeypatch.setattr(manager.store, 'record', fail_once)
        await manager.start()
        try:
            await until(lambda: manager.store.get(export['id'])['status'] == 'failed')
            assert manager.store.get(export['id'])['receipt'] is None
            manager.store.retry(export['id'])
            manager.wake.set()
            await until(lambda: manager.store.get(export['id'])['status'] == 'delivered')
            assert list(remote) == [export['idempotency_key']]
            assert remote[export['idempotency_key']] == [export['content']] * 2
        finally:
            await manager.close()


def test_duplicate_retry_requests_do_not_start_parallel_sends(client, database_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    _, batch_id = prepared(client, database_path)
    entered, release = Event(), Event()
    calls = []
    async def send(export):
        calls.append(export['id'])
        entered.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return DeliveryResult(receipt=receipt(export))
    monkeypatch.setattr(app.state.delivery.client, 'send', send)
    export_id = client.post(f'/api/batches/{batch_id}/export').json()['export']['id']
    try:
        assert entered.wait(2)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: client.post(f'/api/exports/{export_id}/deliver'), range(8)))
        assert all(result.status_code == 202 for result in results)
        assert all(result.json()['status'] == 'delivering' for result in results)
        assert calls == [export_id]
    finally:
        release.set()
    assert finished(client, export_id)['attempts'] == 1
