"""Immutable export membership, recovery identity and transactional failure boundaries."""
import csv
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.exports import ExportStore, MissingOCRFields
from app.db import Database
from conftest import application, csv_bytes, upload


OCR = {'full_name': '王,小明', 'id_number': 'TEST-ID-77', 'dob': '2000-01-01',
       'issue_date': '2020-01-01', 'confidence': .94}
COLUMNS = ['case_ref', 'applicant_name', 'id_number', 'dob', 'issue_date', 'image_ref',
           'ocr_full_name', 'ocr_id_number', 'ocr_dob', 'ocr_issue_date', 'ocr_confidence']


def prepared(client, database_path, states=('stage2_approved',)):
    batch = upload(client, csv_bytes(*(application(f'A-{i}') for i in range(len(states))))).json()
    database = Database(database_path)
    with database.connect() as connection:
        for i, state in enumerate(states):
            connection.execute('UPDATE cases SET status=?,ocr_fields=? WHERE batch_id=? AND case_ref=?',
                               (state, json.dumps(OCR), batch['id'], f'A-{i}'))
    return ExportStore(database), batch['id']


def test_exact_membership_bytes_and_no_second_export(client, database_path):
    states = ('pending', 'ocr_done', 'ocr_failed', 'stage1_approved', 'stage1_rejected',
              'stage2_approved', 'stage2_rejected', 'exported', 'stage2_approved')
    store, batch_id = prepared(client, database_path, states)
    result = store.create(batch_id)
    export = result['export']
    assert result['count'] == export['row_count'] == 2
    assert export['idempotency_key'] == 'export:' + export['id']
    assert export['sha256'] == hashlib.sha256(export['content']).hexdigest()
    rows = list(csv.DictReader(io.StringIO(export['content'].decode())))
    assert list(rows[0]) == COLUMNS
    assert [row['case_ref'] for row in rows] == ['A-5', 'A-8']
    assert rows[0]['ocr_full_name'] == '王,小明'
    assert rows[0]['ocr_confidence'] == '0.94'
    assert b'\r\n' not in export['content']
    assert store.create(batch_id) == {'count': 0, 'export': None}
    current = client.get(f'/api/batches/{batch_id}/cases').json()['items']
    assert [item['status'] for item in current] == [
        'exported' if state == 'stage2_approved' else state for state in states]
    # The persisted snapshot stays identical even if an external DB edit changes source data.
    with store.database.connect() as connection:
        connection.execute("UPDATE cases SET applicant_name='changed' WHERE batch_id=?", (batch_id,))
    assert store.get(export['id'])['content'] == export['content']


def test_new_approval_gets_distinct_export_and_key(client, database_path):
    store, batch_id = prepared(client, database_path, ('stage2_approved', 'stage1_approved'))
    first = store.create(batch_id)['export']
    case = client.get(f'/api/batches/{batch_id}/cases').json()['items'][1]
    assert client.post(f"/api/cases/{case['id']}/review", json={'stage': 2, 'decision': 'approve'}).status_code == 200
    second = store.create(batch_id)['export']
    assert first['id'] != second['id']
    assert first['idempotency_key'] != second['idempotency_key']
    assert first['sha256'] != second['sha256']
    assert [item['id'] for item in store.list_batch(batch_id)] == [first['id'], second['id']]


def test_concurrent_exports_have_one_membership(client, database_path):
    store, batch_id = prepared(client, database_path, ('stage2_approved',) * 8)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.create(batch_id), range(8)))
    assert sorted(result['count'] for result in results) == [0] * 7 + [8]
    with store.database.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM export_cases').fetchone()[0] == 8


def test_missing_ocr_rolls_back_entire_export(client, database_path):
    store, batch_id = prepared(client, database_path, ('stage2_approved',) * 2)
    with store.database.connect() as connection:
        connection.execute("UPDATE cases SET ocr_fields=NULL WHERE case_ref='A-1'")
    with pytest.raises(MissingOCRFields):
        store.create(batch_id)
    assert store.list_batch(batch_id) == []
    assert client.get(f'/api/batches/{batch_id}').json()['status_counts']['stage2_approved'] == 2


def test_transaction_failure_does_not_consume_approvals(client, database_path):
    import sqlite3
    store, batch_id = prepared(client, database_path)
    with store.database.connect() as connection:
        connection.execute("CREATE TRIGGER fail_export BEFORE UPDATE OF status ON cases "
                           "WHEN NEW.status='exported' BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.create(batch_id)
    assert store.list_batch(batch_id) == []
    assert client.get(f'/api/batches/{batch_id}').json()['status_counts']['stage2_approved'] == 1


def test_unknown_and_empty_routes(client, database_path):
    assert client.post('/api/batches/missing/export').status_code == 404
    assert client.get('/api/batches/missing/exports').status_code == 404
    for suffix in ('', '/download'):
        assert client.get('/api/exports/missing' + suffix).status_code == 404
    assert client.post('/api/exports/missing/deliver').status_code == 404
    _, batch_id = prepared(client, database_path, ('pending',))
    response = client.post(f'/api/batches/{batch_id}/export')
    assert response.status_code == 200
    assert response.json() == {'count': 0, 'export': None}
