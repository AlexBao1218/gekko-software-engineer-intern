from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app.main import app
from conftest import application, csv_bytes, upload


def test_insert_failure_rolls_back_entire_batch(client, database_path):
    # A test-only SQLite trigger fails after the first case was written.
    with sqlite3.connect(database_path) as connection:
        connection.execute('''CREATE TRIGGER fail_second_case BEFORE INSERT ON cases
            WHEN NEW.case_ref = 'A-2' BEGIN
                SELECT RAISE(ABORT, 'injected storage failure');
            END''')
    response = upload(client, csv_bytes(application('A-1'), application('A-2')))
    assert response.status_code == 500
    assert response.json()['detail']['code'] == 'storage_error'
    assert 'injected storage failure' not in response.text
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM cases').fetchone()[0] == 0


@pytest.mark.parametrize(('column', 'value'), [
    ('case_ref', 'A-1'), ('batch_id', 'missing'), ('applicant_name', ''), ('status', 'invented'),
])
def test_database_guards_against_invalid_case_writes(client, column, value):
    batch = upload(client, csv_bytes(application('A-1'), application('A-2'))).json()
    with pytest.raises(sqlite3.IntegrityError):
        with app.state.database.connect() as connection:
            # column comes from literal test cases, never from a request.
            connection.execute(f'UPDATE cases SET {column} = ? WHERE batch_id = ? AND case_ref = ?',
                               (value, batch['id'], 'A-2'))
    page = client.get(f"/api/batches/{batch['id']}/cases").json()
    assert [case['case_ref'] for case in page['items']] == ['A-1', 'A-2']
    assert all(case['status'] == 'pending' for case in page['items'])


def test_locked_database_returns_retryable_error_without_partial_batch(client, database_path):
    connection = sqlite3.connect(database_path)
    try:
        connection.execute('BEGIN EXCLUSIVE')
        response = upload(client, csv_bytes(application()))
        assert response.status_code == 503
        assert response.headers['Retry-After'] == '1'
        assert response.json()['detail']['code'] == 'storage_unavailable'
        assert str(database_path) not in response.text
    finally:
        connection.rollback()
        connection.close()
    assert upload(client, csv_bytes(application())).status_code == 201
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0] == 1


def test_simultaneous_imports_create_two_complete_isolated_batches(client, database_path):
    content = csv_bytes(application(), application('A-2'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: upload(client, content), range(2)))
    assert [response.status_code for response in responses] == [201, 201]
    assert len({response.json()['id'] for response in responses}) == 2
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0] == 2
        assert connection.execute('SELECT COUNT(*) FROM cases').fetchone()[0] == 4
