"""Real HTTP and process-restart check; no Docker or existing local service."""
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import httpx

from conftest import application, csv_bytes


ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def api_process(listener, database_path, log_path):
    environment = {**os.environ, 'DATABASE_PATH': str(database_path)}
    environment.pop('PYTHONPATH', None)
    with log_path.open('w') as log:
        process = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'app.main:app', '--app-dir', 'q1_api',
             '--fd', str(listener.fileno())],
            cwd=ROOT, env=environment, stdout=log, stderr=log,
            pass_fds=(listener.fileno(),))
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{listener.getsockname()[1]}',
                              timeout=1, trust_env=False) as client:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    assert process.poll() is None, log_path.read_text()
                    try:
                        if client.get('/api/health').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise AssertionError('API startup timed out: ' + log_path.read_text())
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_native_upload_survives_real_process_restart(tmp_path):
    database_path = tmp_path / 'persistent.sqlite3'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        with api_process(listener, database_path, tmp_path / 'first.log') as client:
            response = client.post('/api/batches', files={
                'file': ('sample.csv', (ROOT / 'data/account_applications.csv').read_bytes(), 'text/csv')})
            assert response.status_code == 201
            batch = response.json()
            assert (batch['accepted_count'], batch['rejected_count']) == (43, 7)
        with api_process(listener, database_path, tmp_path / 'second.log') as client:
            assert client.get(f"/api/batches/{batch['id']}").json() == batch
            page = client.get(f"/api/batches/{batch['id']}/cases?page_size=100").json()
            assert page['total'] == len(page['items']) == 43
            assert len({case['id'] for case in page['items']}) == 43


def test_native_two_stage_review_survives_process_restart(tmp_path):
    database_path = tmp_path / 'review.sqlite3'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        with api_process(listener, database_path, tmp_path / 'review-first.log') as client:
            batch = client.post('/api/batches', files={
                'file': ('case.csv', csv_bytes(application()), 'text/csv')}).json()
            case = client.get(f"/api/batches/{batch['id']}/cases").json()['items'][0]
            url = f"/api/cases/{case['id']}/review"
            assert client.post(url, json={'stage': 1, 'decision': 'approve'}).status_code == 409
            # Only a test fixture supplies OCR completion until Q2 is implemented.
            with sqlite3.connect(database_path) as connection:
                connection.execute("UPDATE cases SET status = 'ocr_done' WHERE id = ?", (case['id'],))
            assert client.post(url, json={'stage': 2, 'decision': 'approve'}).status_code == 409
            assert client.post(url, json={'stage': 1, 'decision': 'approve'}).json()['status'] == 'stage1_approved'
            assert client.post(url, json={'stage': 2, 'decision': 'approve'}).json()['status'] == 'stage2_approved'
        with api_process(listener, database_path, tmp_path / 'review-second.log') as client:
            assert client.get(f"/api/batches/{batch['id']}").json()['status_counts']['stage2_approved'] == 1
            assert client.post(url, json={'stage': 2, 'decision': 'approve'}).status_code == 409
        with sqlite3.connect(database_path) as connection:
            records = connection.execute('SELECT stage, decision FROM reviews ORDER BY id').fetchall()
            assert records == [(1, 'approve'), (2, 'approve')]
