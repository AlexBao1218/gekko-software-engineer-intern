from pathlib import Path
import sqlite3

import pytest

from conftest import FIELDS, application, csv_bytes, upload


def test_sample_reports_exact_bad_rows_and_reasons(client):
    sample = Path(__file__).resolve().parents[2] / 'data/account_applications.csv'
    response = upload(client, sample.read_bytes())
    assert response.status_code == 201
    batch = response.json()
    assert (batch['total_rows'], batch['accepted_count'], batch['rejected_count']) == (50, 43, 7)
    assert [row['row'] for row in batch['rejected_rows']] == [6, 13, 18, 19, 28, 41, 48]
    assert [row['errors'][0]['field'] for row in batch['rejected_rows']] == [
        'dob', 'applicant_name', 'case_ref', 'dob', 'dob', 'applicant_name', 'case_ref']
    assert all(error['message'] for row in batch['rejected_rows'] for error in row['errors'])
    assert batch['status_counts'] == {
        'pending': 43, 'ocr_done': 0, 'ocr_failed': 0, 'stage1_approved': 0,
        'stage1_rejected': 0, 'stage2_approved': 0, 'stage2_rejected': 0, 'exported': 0}


@pytest.mark.parametrize(('field', 'value'), [
    ('dob', '2023-02-29'), ('issue_date', '2020-13-01'), ('dob', '20000101'),
    ('issue_date', '2020-1-1'), ('applicant_name', '  '), ('case_ref', ''),
    ('image_ref', ''), ('id_number', 'REAL-123'),
])
def test_bad_row_rejected_but_valid_row_kept(client, field, value):
    values = {'case_ref': 'BAD', field: value}
    result = upload(client, csv_bytes(application(**values), application('GOOD')))
    assert result.status_code == 201
    batch = result.json()
    assert (batch['accepted_count'], batch['rejected_count']) == (1, 1)
    assert batch['rejected_rows'][0]['row'] == 2
    assert field in [error['field'] for error in batch['rejected_rows'][0]['errors']]


def test_multiple_errors_count_as_one_rejected_row(client):
    batch = upload(client, csv_bytes(application(dob='unknown', applicant_name=''))).json()
    assert (batch['total_rows'], batch['accepted_count'], batch['rejected_count']) == (1, 0, 1)
    assert {e['field'] for e in batch['rejected_rows'][0]['errors']} == {'dob', 'applicant_name'}


def test_first_occurrence_reserves_reference_even_if_invalid(client):
    batch = upload(client, csv_bytes(application(dob='bad'), application())).json()
    assert (batch['accepted_count'], batch['rejected_count']) == (0, 2)
    assert batch['rejected_rows'][1]['errors'][0]['field'] == 'case_ref'


def test_bom_whitespace_and_reordered_columns_are_accepted(client):
    fields = list(reversed(FIELDS))
    content = b'\xef\xbb\xbf' + csv_bytes(list(reversed(application(applicant_name='  陳, Alex  '))), fields=fields)
    response = upload(client, content)
    assert response.status_code == 201
    assert response.json()['accepted_count'] == 1


def test_physical_line_numbers_survive_blank_and_multiline_records(client):
    content = csv_bytes(application(applicant_name='Test\nApplicant'))
    content += b'\r\n' + csv_bytes(application('BAD', dob='bad')).split(b'\r\n', 1)[1]
    batch = upload(client, content).json()
    assert batch['total_rows'] == 2
    assert batch['rejected_rows'][0]['row'] == 5


@pytest.mark.parametrize('content', [
    b'', b'\xff\xfe', b'case_ref,applicant_name\nA,B\n',
    csv_bytes(fields=FIELDS + ['extra']), csv_bytes(fields=FIELDS + ['dob']),
    csv_bytes(), csv_bytes() + b'\r\n',
    csv_bytes(application()) + b'only,two\n',
    csv_bytes(application()) + b'"unterminated',
    csv_bytes(application(applicant_name='x' * 140_000)),
], ids=['empty', 'encoding', 'missing-header', 'extra-header', 'duplicate-header',
        'header-only', 'blank-only', 'ragged-tail', 'bad-quotes', 'long-field'])
def test_file_errors_are_400_without_partial_import(client, database_path, content):
    response = upload(client, content)
    assert response.status_code == 400
    assert response.json()['detail']['code'] == 'invalid_csv'
    assert response.json()['detail']['message']
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM cases').fetchone()[0] == 0


def test_oversize_upload_is_413(client):
    assert upload(client, b'x' * (2 * 1024 * 1024 + 1)).status_code == 413


def test_missing_file_is_422(client):
    assert client.post('/api/batches').status_code == 422


@pytest.mark.parametrize('tail', [b'""\n', b'"   "\n'], ids=['quoted-empty', 'quoted-space'])
def test_one_column_quoted_record_is_not_mistaken_for_blank_line(client, tail):
    response = upload(client, csv_bytes(application()) + tail)
    assert response.status_code == 400
    assert response.json()['detail']['row'] == 3


def test_nul_data_is_rejected_without_storage_error(client, database_path):
    content = csv_bytes(application(), application('\x00invalid'))
    response = upload(client, content)
    assert response.status_code == 400
    assert response.json()['detail']['code'] == 'invalid_csv'
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM batches').fetchone()[0] == 0
