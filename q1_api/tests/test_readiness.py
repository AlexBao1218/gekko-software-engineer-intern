"""Readiness must test the existing writable database without repairing or changing it."""
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import application, csv_bytes, upload


def assert_unready(client, database_path):
    response = client.get('/api/ready')
    assert response.status_code == 503
    assert response.json()['status'] == 'not_ready'
    assert str(database_path) not in response.text
    assert 'Traceback' not in response.text
    assert client.get('/api/health').json() == {'status': 'ok'}


def test_ready_reads_schema_and_writes_without_changing_business_data(client, database_path):
    batch = upload(client, csv_bytes(application())).json()
    with sqlite3.connect(database_path) as connection:
        before = connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
    for _ in range(3):
        response = client.get('/api/ready')
        assert response.status_code == 200
        assert response.json() == {'status': 'ready'}
    assert client.get(f"/api/batches/{batch['id']}").json() == batch
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall() == before


def test_missing_database_is_not_created_by_readiness(client, database_path):
    client.portal.call(app.state.delivery.close)
    saved = database_path.with_suffix('.saved')
    database_path.rename(saved)
    try:
        assert_unready(client, database_path)
        assert not database_path.exists()
    finally:
        saved.rename(database_path)
    assert client.get('/api/ready').status_code == 200


@pytest.mark.parametrize('failure', ['empty', 'corrupt', 'schema_missing'])
def test_existing_but_unusable_database_does_not_report_ready(client, database_path, failure):
    client.portal.call(app.state.delivery.close)
    saved = database_path.with_suffix('.saved')
    database_path.rename(saved)
    try:
        if failure == 'empty':
            database_path.touch()
        elif failure == 'corrupt':
            database_path.write_bytes(b'not a SQLite database')
        else:
            with sqlite3.connect(database_path) as connection:
                connection.execute('CREATE TABLE batches (id TEXT)')
        assert_unready(client, database_path)
    finally:
        database_path.unlink(missing_ok=True)
        saved.rename(database_path)
    assert client.get('/api/ready').status_code == 200


def test_writer_lock_returns_bounded_503_then_recovers(client, database_path):
    with sqlite3.connect(database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        started = time.monotonic()
        assert_unready(client, database_path)
        assert time.monotonic() - started < 1.5
    assert client.get('/api/ready').status_code == 200


def test_read_only_file_is_unready_but_liveness_survives(client, database_path):
    client.portal.call(app.state.delivery.close)
    mode = database_path.stat().st_mode
    database_path.chmod(0o444)
    try:
        assert_unready(client, database_path)
    finally:
        database_path.chmod(mode)
    assert client.get('/api/ready').status_code == 200


def test_read_only_parent_prevents_sqlite_journal_and_is_unready(client, database_path):
    client.portal.call(app.state.delivery.close)
    mode = database_path.parent.stat().st_mode
    database_path.parent.chmod(0o555)
    try:
        assert_unready(client, database_path)
    finally:
        database_path.parent.chmod(mode)
    assert client.get('/api/ready').status_code == 200


def test_database_path_with_spaces_and_uri_delimiters(tmp_path, monkeypatch):
    database_path = tmp_path / 'application ? # & 中文.sqlite3'
    monkeypatch.setenv('DATABASE_PATH', str(database_path))
    with TestClient(app) as client:
        assert client.get('/api/ready').status_code == 200


def test_configured_seed_is_available_before_ready_and_not_duplicated(database_path, monkeypatch):
    from pathlib import Path
    sample = Path(__file__).resolve().parents[2] / 'data/account_applications.csv'
    monkeypatch.setenv('SEED_CSV_PATH', str(sample))
    ids = []
    for _ in range(2):
        with TestClient(app) as client:
            ready = client.get('/api/ready')
            assert ready.status_code == 200
            seed_id = ready.json().get('seed_batch_id')
            assert seed_id is not None
            ids.append(seed_id)
            batch = client.get('/api/batches/' + seed_id).json()
            assert (batch['accepted_count'], batch['rejected_count']) == (43, 7)
            assert batch['status_counts']['pending'] == 43
    assert ids[0] == ids[1]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0] == 1
    monkeypatch.delenv('SEED_CSV_PATH')
    with TestClient(app) as client:
        assert client.get('/api/ready').json() == {'status': 'ready'}


def test_configured_missing_seed_fails_startup(database_path, monkeypatch):
    monkeypatch.setenv('SEED_CSV_PATH', str(database_path.parent / 'missing.csv'))
    with pytest.raises(ValueError, match='Seed CSV'):
        with TestClient(app):
            pass


@pytest.mark.parametrize('table', ['ocr_attempts', 'export_cases', 'delivery_attempts'])
def test_missing_workflow_table_must_not_report_ready(client, database_path, table):
    client.portal.call(app.state.delivery.close)
    with sqlite3.connect(database_path) as connection:
        connection.execute(f'DROP TABLE {table}')
    assert_unready(client, database_path)
