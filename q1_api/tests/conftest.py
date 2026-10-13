import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app


FIELDS = ['case_ref', 'applicant_name', 'id_number', 'dob', 'issue_date', 'image_ref']


def csv_bytes(*rows, fields=FIELDS):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(fields)
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def application(case_ref='A-1', **changes):
    row = dict(zip(FIELDS, [case_ref, 'Test Applicant', 'TEST-ID-123',
                           '2000-02-29', '2020-01-01', 'scan.jpg']))
    row.update(changes)
    return [row[field] for field in FIELDS]


@pytest.fixture
def database_path(tmp_path, monkeypatch):
    path = tmp_path / 'app.sqlite3'
    monkeypatch.setenv('DATABASE_PATH', str(path))
    return path


@pytest.fixture
def client(database_path):
    with TestClient(app) as client:
        yield client


def upload(client, content):
    return client.post('/api/batches', files={'file': ('applications.csv', content, 'text/csv')})
