"""Exercise the deployed smoke executable against a small HTTP protocol fixture.

These tests protect its failure detection, not the API business implementation;
the native lifecycle tests and the real Compose smoke cover that implementation.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'q3_deploy/scripts/smoke.py'
SEED_ID = '00000000-0000-4000-8000-000000000001'
BATCH_ID = '00000000-0000-4000-8000-000000000002'
CASE = dict(id='case-1', batch_id=BATCH_ID, case_ref='A-1', applicant_name='Test Applicant',
            id_number='TEST-ID-123', dob='2000-02-29', issue_date='2020-01-01',
            image_ref='scan.jpg', status='ocr_done',
            ocr_fields=dict(full_name='OCR Applicant', id_number='TEST-ID-123',
                            dob='2000-02-29', issue_date='2020-01-01', confidence=0.95))
CSV = (b'case_ref,applicant_name,id_number,dob,issue_date,image_ref,ocr_full_name,'
       b'ocr_id_number,ocr_dob,ocr_issue_date,ocr_confidence\n'
       b'A-1,Test Applicant,TEST-ID-123,2000-02-29,2020-01-01,scan.jpg,OCR Applicant,'
       b'TEST-ID-123,2000-02-29,2020-01-01,0.95\n')
SHA = hashlib.sha256(CSV).hexdigest()
RECEIPT = dict(delivery_id='delivery-1', idempotency_key='export:export-1',
               file_name='export-1.csv', sha256=SHA, ingested_at=1, attempts=2)
EXPORT = dict(id='export-1', batch_id=BATCH_ID, file_name='export-1.csv',
              idempotency_key='export:export-1', sha256=SHA, row_count=1,
              status='delivered', attempts=2, receipt=RECEIPT)


@pytest.fixture
def endpoint():
    state = {'fault': None, 'exports': 0, 'requests': []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            state['requests'].append((self.command, self.path, body))
            fault = state['fault']
            status = 200
            batch = dict(id=BATCH_ID, accepted_count=43, rejected_count=7,
                         total_rows=50, status_counts={'ocr_done': 38, 'ocr_failed': 5},
                         ocr_run={'status': 'completed'})
            payload = dict(EXPORT, receipt=dict(RECEIPT))
            if fault == 'receipt':
                payload['receipt']['sha256'] = '0' * 64
            # Internally consistent hash/receipt still must not excuse the wrong case.
            if fault == 'wrong-case':
                payload['sha256'] = hashlib.sha256(CSV.replace(b'A-1,', b'B-2,')).hexdigest()
                payload['receipt']['sha256'] = payload['sha256']
            if self.path == '/api/ready':
                if fault == 'timeout':
                    time.sleep(0.2)
                status = 503 if fault == 'http' else 200
                payload = {'status': 'ready', 'seed_batch_id': SEED_ID}
            elif self.path == f'/api/batches/{SEED_ID}':
                payload = dict(batch, id=SEED_ID)
            elif self.path == '/api/batches':
                status, payload = 201, batch
                if b'name="file"' not in body or b'case_ref' not in body:
                    status = 400
            elif self.path.endswith('/process'):
                status, payload = 202, batch
            elif self.path == f'/api/batches/{BATCH_ID}':
                payload = batch
                if fault == 'stalled':
                    payload['ocr_run']['status'] = 'running'
                if fault == 'ocr-failed':
                    payload['ocr_run']['status'] = 'failed'
            elif '/cases?' in self.path:
                payload = {'items': [CASE], 'total': 1, 'page': 1, 'page_size': 100}
            elif self.path.endswith('/review'):
                review = json.loads(body)
                status = 200 if review['decision'] == 'approve' else 409
                payload = dict(CASE, status=f"stage{review['stage']}_approved")
            elif self.path.endswith('/export'):
                state['exports'] += 1
                status = 202 if state['exports'] == 1 else 200
                payload = {'count': 1, 'export': EXPORT} if status == 202 else {'count': 0, 'export': None}
            elif self.path.endswith('/download'):
                payload = CSV + (b'changed' if fault == 'download' else b'')
                if fault == 'wrong-case':
                    payload = CSV.replace(b'A-1,', b'B-2,')
            elif self.path == '/debug/deliveries':
                records = [dict(RECEIPT)]
                if fault == 'duplicate':
                    records.append(dict(RECEIPT, delivery_id='delivery-2'))
                if fault == 'debug-hash':
                    records[0]['sha256'] = '0' * 64
                payload = records
            elif self.path != '/api/exports/export-1':
                status, payload = 404, {'error': 'unexpected path'}
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', state
    server.shutdown()
    server.server_close()
    thread.join()


def run_smoke(endpoint):
    base, state = endpoint
    return subprocess.run(
        [sys.executable, str(SCRIPT), '--base', base, '--csv', str(ROOT / 'data/account_applications.csv'),
         '--request-timeout', '0.05', '--ocr-timeout', '0.1', '--delivery-timeout', '0.1',
         '--poll-interval', '0.01'],
        env={**os.environ, 'DELIVERY_URL': base + '/ingest'},
        capture_output=True, text=True, timeout=4)


def test_smoke_verifies_exact_export_receipt_and_single_ingestion(endpoint):
    result = run_smoke(endpoint)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary['pass'] is True
    assert summary['sha256'] == SHA
    assert summary['receipt']['delivery_id'] == 'delivery-1'
    assert summary['batch_id'] == BATCH_ID != SEED_ID
    requests = endpoint[1]['requests']
    assert sum(path.endswith('/export') for _, path, _ in requests) == 2
    assert not any('/reset' in path for _, path, _ in requests)


@pytest.mark.parametrize(('fault', 'message'), [
    ('receipt', 'receipt'), ('download', 'SHA-256'), ('duplicate', 'one ingestion'),
    ('debug-hash', 'downstream'), ('http', 'HTTP 503'), ('timeout', 'request failed'),
    ('stalled', 'deadline'), ('ocr-failed', 'OCR run'), ('wrong-case', 'exactly the approved case'),
])
def test_smoke_fails_with_actionable_error_on_broken_evidence(endpoint, fault, message):
    endpoint[1]['fault'] = fault
    result = run_smoke(endpoint)
    assert result.returncode == 1, result.stderr
    summary = json.loads(result.stderr)
    assert summary['pass'] is False
    assert message in summary['error']
    if fault in {'http', 'timeout', 'stalled', 'ocr-failed'}:
        assert endpoint[1]['exports'] == 0
