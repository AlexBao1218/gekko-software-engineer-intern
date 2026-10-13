from fastapi.testclient import TestClient
import sqlite3
import pytest

from app.main import app
from conftest import application, csv_bytes, upload


def test_batch_details_and_filtered_pagination(client):
    created = upload(client, csv_bytes(*(application(f'A-{n}') for n in range(5)))).json()
    batch_id = created['id']
    assert client.get(f'/api/batches/{batch_id}').json() == created
    first = client.get(f'/api/batches/{batch_id}/cases?page_size=2').json()
    second = client.get(f'/api/batches/{batch_id}/cases?page=2&page_size=2').json()
    assert (first['total'], first['page'], first['page_size']) == (5, 1, 2)
    assert [c['case_ref'] for c in first['items']] == ['A-0', 'A-1']
    assert [c['case_ref'] for c in second['items']] == ['A-2', 'A-3']
    assert all(c['status'] == 'pending' for c in first['items'])
    assert first['items'][0]['row_number'] == 2
    assert first['items'][0]['batch_id'] == batch_id
    assert client.get(f'/api/batches/{batch_id}/cases?status=pending').json()['total'] == 5
    empty = client.get(f'/api/batches/{batch_id}/cases?status=ocr_failed').json()
    assert empty['total'] == 0 and empty['items'] == []
    assert client.get(f'/api/batches/{batch_id}/cases?page=10').json()['items'] == []


def test_same_file_in_two_batches_does_not_merge_or_mix_cases(client):
    content = csv_bytes(application())
    first = upload(client, content).json()
    second = upload(client, content).json()
    assert first['id'] != second['id']
    cases = [client.get(f"/api/batches/{b['id']}/cases").json()['items'] for b in (first, second)]
    assert len(cases[0]) == len(cases[1]) == 1
    assert cases[0][0]['id'] != cases[1][0]['id']


def test_whitespace_is_normalized_and_csv_quotes_are_preserved(client):
    batch = upload(client, csv_bytes(application(' A-1 ', applicant_name='  陳, "Alex"  '))).json()
    item = client.get(f"/api/batches/{batch['id']}/cases").json()['items'][0]
    assert item['case_ref'] == 'A-1'
    assert item['applicant_name'] == '陳, "Alex"'
    assert (item['dob'], item['issue_date'], item['id_number']) == ('2000-02-29', '2020-01-01', 'TEST-ID-123')


@pytest.mark.parametrize('query', ['page=0', 'page=-1', 'page_size=0', 'page_size=101', 'page=x', 'status=unknown'])
def test_invalid_list_parameters_are_422(client, query):
    batch = upload(client, csv_bytes(application())).json()
    assert client.get(f"/api/batches/{batch['id']}/cases?{query}").status_code == 422


@pytest.mark.parametrize('suffix', ['', '/cases'])
def test_unknown_batch_is_404(client, suffix):
    response = client.get('/api/batches/missing' + suffix)
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'batch_not_found'


def test_data_and_rejections_survive_application_restart(database_path):
    with TestClient(app) as first:
        batch = upload(first, csv_bytes(application(), application('BAD', dob='invalid'))).json()
    with TestClient(app) as second:
        assert second.get(f"/api/batches/{batch['id']}").json() == batch
        assert second.get(f"/api/batches/{batch['id']}/cases").json()['items'][0]['case_ref'] == 'A-1'


def test_counts_and_filter_reflect_stored_status(client, database_path):
    batch = upload(client, csv_bytes(application(), application('A-2'))).json()
    # Fixture isolates query behavior from review/OCR state transitions.
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE cases SET status = 'ocr_done' WHERE case_ref = 'A-2'")
    detail = client.get(f"/api/batches/{batch['id']}").json()
    assert detail['accepted_count'] == 2
    assert detail['status_counts']['pending'] == detail['status_counts']['ocr_done'] == 1
    page = client.get(f"/api/batches/{batch['id']}/cases?status=ocr_done").json()
    assert page['total'] == 1
    assert [case['case_ref'] for case in page['items']] == ['A-2']


def test_very_large_page_is_empty_not_database_integer_overflow(client):
    batch = upload(client, csv_bytes(application())).json()
    response = client.get(f"/api/batches/{batch['id']}/cases?page=99999999999999999999999999999")
    assert response.status_code == 200
    assert response.json()['items'] == []
