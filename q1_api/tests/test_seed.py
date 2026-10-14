from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest

from app.db import Database
from app.exports import ExportStore
from conftest import application, csv_bytes


SAMPLE = Path(__file__).resolve().parents[2] / 'data/account_applications.csv'


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / 'seed.sqlite3')
    database.initialize()
    return database


def bootstrap(database, path):
    from app.seed import bootstrap_seed
    return bootstrap_seed(database, path)


def snapshot(database):
    with database.connect() as connection:
        return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
                for table in ('batches', 'cases', 'reviews', 'exports', 'export_cases',
                              'delivery_attempts', 'ocr_runs', 'seed_imports')}


def assert_no_import(database):
    with database.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM cases').fetchone()[0] == 0
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='seed_imports'").fetchone():
            assert connection.execute('SELECT COUNT(*) FROM seed_imports').fetchone()[0] == 0


def test_seed_imports_sample_with_canonical_rejections_and_no_ocr(database):
    batch_id = bootstrap(database, SAMPLE)
    batch = database.get_batch(batch_id)
    assert (batch['total_rows'], batch['accepted_count'], batch['rejected_count']) == (50, 43, 7)
    assert [row['row'] for row in batch['rejected_rows']] == [6, 13, 18, 19, 28, 41, 48]
    assert [row['errors'][0]['field'] for row in batch['rejected_rows']] == [
        'dob', 'applicant_name', 'case_ref', 'dob', 'dob', 'applicant_name', 'case_ref']
    assert batch['status_counts']['pending'] == 43
    assert batch['ocr_run'] is None
    with database.connect() as connection:
        marker = dict(connection.execute('SELECT * FROM seed_imports').fetchone())
        assert marker == {'seed_key': 'account_applications_v1', 'batch_id': batch_id,
                          'sha256': hashlib.sha256(SAMPLE.read_bytes()).hexdigest()}


def test_restart_returns_original_batch_without_writes(database):
    batch_id = bootstrap(database, SAMPLE)
    before = snapshot(database)
    restarted = Database(database.path)
    restarted.initialize()
    assert bootstrap(restarted, SAMPLE) == batch_id
    assert snapshot(restarted) == before


def test_restart_preserves_reviews_export_bytes_and_delivery_receipt(database):
    batch_id = bootstrap(database, SAMPLE)
    cases = database.list_cases(batch_id, None, 1, 100)['items']
    fields = json.dumps({'full_name': 'Test Applicant', 'id_number': 'TEST-ID-123',
                         'dob': '2000-02-29', 'issue_date': '2020-01-01', 'confidence': 0.99})
    with database.connect() as connection:
        connection.executemany("UPDATE cases SET status='ocr_done',ocr_fields=? WHERE id=?",
                               [(fields, case['id']) for case in cases[:2]])
    for case in cases[:2]:
        database.review_case(case['id'], 1, 'approve')
    database.review_case(cases[0]['id'], 2, 'approve')
    export = ExportStore(database).create(batch_id)['export']
    with database.connect() as connection:
        connection.execute("UPDATE exports SET status='delivered',receipt=? WHERE id=?",
                           (json.dumps({'receipt_id': 'seed-receipt'}), export['id']))
    before = snapshot(database)
    assert bootstrap(Database(database.path), SAMPLE) == batch_id
    assert snapshot(database) == before
    statuses = {case['id']: case['status'] for case in database.list_cases(batch_id, None, 1, 100)['items']}
    assert statuses[cases[0]['id']] == 'exported'
    assert statuses[cases[1]['id']] == 'stage1_approved'


def test_concurrent_starters_create_exactly_one_batch(database):
    barrier = Barrier(4)

    def start(_):
        barrier.wait(timeout=5)
        return bootstrap(Database(database.path), SAMPLE)

    with ThreadPoolExecutor(max_workers=4) as executor:
        batch_ids = list(executor.map(start, range(4)))
    assert len(set(batch_ids)) == 1
    with database.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM cases').fetchone()[0] == 43
        assert connection.execute('SELECT COUNT(*) FROM seed_imports').fetchone()[0] == 1


def test_case_insert_failure_rolls_back_batch_marker_and_prior_cases(database):
    with database.connect() as connection:
        connection.execute('''CREATE TRIGGER fail_second_seed_case BEFORE INSERT ON cases
            WHEN (SELECT COUNT(*) FROM cases) = 1
            BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='injected insert failure'):
        bootstrap(database, SAMPLE)
    assert_no_import(database)
    with database.connect() as connection:
        connection.execute('DROP TRIGGER fail_second_seed_case')
    assert database.get_batch(bootstrap(database, SAMPLE))['accepted_count'] == 43


def test_changed_source_fails_without_replacing_existing_seed(database, tmp_path):
    bootstrap(database, SAMPLE)
    before = snapshot(database)
    changed = tmp_path / 'changed.csv'
    changed.write_bytes(csv_bytes(application(applicant_name='PRIVATE SOURCE CONTENT')))
    with pytest.raises(ValueError, match='changed') as error:
        bootstrap(database, changed)
    assert 'PRIVATE SOURCE CONTENT' not in str(error.value)
    assert snapshot(database) == before


@pytest.mark.parametrize('already_seeded', [False, True])
def test_missing_source_fails_even_on_restart(database, tmp_path, already_seeded):
    if already_seeded:
        bootstrap(database, SAMPLE)
        before = snapshot(database)
    with pytest.raises(ValueError, match='read'):
        bootstrap(database, tmp_path / 'missing.csv')
    if already_seeded:
        assert snapshot(database) == before
    else:
        assert_no_import(database)


@pytest.mark.parametrize('content', [b'', b'\xff', b'wrong,header\n', csv_bytes(),
                                  csv_bytes(application()) + b'"unterminated'])
def test_invalid_csv_fails_without_partial_import(database, tmp_path, content):
    source = tmp_path / 'bad.csv'
    source.write_bytes(content)
    with pytest.raises(ValueError, match='CSV'):
        bootstrap(database, source)
    assert_no_import(database)


def test_seed_marker_enforces_batch_reference(database):
    batch_id = bootstrap(database, SAMPLE)
    with database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute('UPDATE seed_imports SET batch_id=? WHERE batch_id=?',
                               ('missing-batch', batch_id))


def test_seed_after_existing_user_import_keeps_both_batches(database):
    from app.imports import parse_csv
    original = database.import_batch(parse_csv(csv_bytes(application('USER-1'))))
    seed_id = bootstrap(database, SAMPLE)
    assert seed_id != original['id']
    assert database.get_batch(original['id']) == original
    with database.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0] == 2
