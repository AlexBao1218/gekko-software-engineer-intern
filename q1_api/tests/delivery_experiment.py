"""Native delivery evidence with unchanged mocks, including a real SIGKILL.

Run: .venv/bin/python q1_api/tests/delivery_experiment.py --output NEW_DIRECTORY
Every scenario retains its onboarding process and deduplication records throughout.
"""
from contextlib import contextmanager
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

from ocr_experiment import ROOT, SAMPLE, cases, process_round, save, service, upload


COLUMNS = ['case_ref', 'applicant_name', 'id_number', 'dob', 'issue_date', 'image_ref',
           'ocr_full_name', 'ocr_id_number', 'ocr_dob', 'ocr_issue_date', 'ocr_confidence']
API_CONFIG = {'DELIVERY_TIMEOUT_SECONDS': '1', 'DELIVERY_MAX_ATTEMPTS': '4',
              'DELIVERY_BACKOFF_SECONDS': '0.1', 'DELIVERY_BACKOFF_CAP_SECONDS': '0.5'}
OCR_CONFIG = {'OCR_FAIL_RATE': '0', 'OCR_HANG_RATE': '0'}


@contextmanager
def onboarding(directory, config):
    directory.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    with (directory / 'onboarding.log').open('w') as log:
        process = subprocess.Popen(
            [sys.executable, '-u', 'mock_onboarding/server.py', '--port', str(port), '--seed', '7'],
            cwd=ROOT, env={**os.environ, **config}, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=2, trust_env=False) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert process.poll() is None, (directory / 'onboarding.log').read_text()
                    try:
                        if client.get('/online').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.01)
                else:
                    raise AssertionError('Onboarding startup timed out')
                yield client
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


def trace_api(api, result):
    def record(response):
        response.read()
        entry = {'at_epoch': time.time(), 'method': response.request.method,
                 'path': response.request.url.raw_path.decode(), 'status_code': response.status_code}
        if 'application/json' in response.headers.get('content-type', ''):
            entry['response'] = response.json()
        else:
            entry.update(bytes=len(response.content), sha256=hashlib.sha256(response.content).hexdigest())
        result['api_requests'].append(entry)
    api.event_hooks['response'].append(record)


def approve(api, item):
    for stage in (1, 2):
        response = api.post(f"/api/cases/{item['id']}/review", json={'stage': stage, 'decision': 'approve'})
        assert response.status_code == 200, response.text


def create_export(api, batch_id, count):
    response = api.post(f'/api/batches/{batch_id}/export')
    assert response.status_code == (202 if count else 200), response.text
    body = response.json()
    assert body['count'] == count, body
    if not count:
        assert body['export'] is None
    return body['export']


def wait_delivered(api, export_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = api.get(f'/api/exports/{export_id}')
        assert response.status_code == 200, response.text
        detail = response.json()
        if detail['status'] == 'delivered':
            return detail
        assert detail['status'] != 'failed', detail
        time.sleep(0.02)
    raise AssertionError(f'Delivery did not finish: {detail}')


def verify_export(api, detail, selected, destination):
    response = api.get(f"/api/exports/{detail['id']}/download")
    assert response.status_code == 200, response.text
    content = response.content
    destination.write_bytes(content)
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(COLUMNS)
    for item in sorted(selected, key=lambda case: case['case_ref']):
        writer.writerow([item[key] for key in COLUMNS[:6]] +
                        [item['ocr_fields'][key.removeprefix('ocr_')] for key in COLUMNS[6:]])
    assert content == stream.getvalue().encode('utf-8')
    assert hashlib.sha256(content).hexdigest() == detail['sha256']
    assert detail['row_count'] == len(selected)
    assert detail['idempotency_key'] == f"export:{detail['id']}"
    if detail['receipt'] is not None:
        for key in ('sha256', 'file_name', 'idempotency_key'):
            assert detail['receipt'][key] == detail[key]
    return content


def verify_ingestions(records, details):
    assert len(records) == len(details), records
    assert len({row['idempotency_key'] for row in records}) == len(details)
    for detail in details:
        record = next(row for row in records if row['idempotency_key'] == detail['idempotency_key'])
        for key in ('sha256', 'file_name', 'idempotency_key'):
            assert record[key] == detail[key]
        assert record['delivery_id'] == detail['receipt']['delivery_id']


def run_scenario(directory, scenario):
    directory.mkdir(parents=True, exist_ok=True)
    mock_config = {'ONBOARDING_FAIL_RATE': '0', 'ONBOARDING_LOST_RATE': '0',
                   'ONBOARDING_LOST_SECONDS': '0.5'}
    api_config = dict(API_CONFIG)
    if scenario in ('lost-response', 'sigkill'):
        mock_config['ONBOARDING_LOST_RATE'] = '1'
        api_config['DELIVERY_TIMEOUT_SECONDS'] = '0.1'
    if scenario == 'sigkill':
        mock_config['ONBOARDING_LOST_SECONDS'] = '2'
        api_config['DELIVERY_TIMEOUT_SECONDS'] = '3'
    result = {'scenario': scenario, 'mock_seed': 7, 'mock_config': mock_config,
              'api_config': api_config, 'ocr_config': OCR_CONFIG, 'api_requests': [], 'pass': False}
    started = time.monotonic()
    database = directory / 'db.sqlite3'
    try:
        with service('mock', directory, config=OCR_CONFIG) as (ocr, _), onboarding(directory, mock_config) as vendor:
            api_config['DELIVERY_URL'] = str(vendor.base_url).rstrip('/') + '/ingest'
            try:
                with service('api', directory / 'first', database=database,
                             ocr_url=str(ocr.base_url).rstrip('/') + '/ocr/parse', config=api_config) as (api, process):
                    trace_api(api, result)
                    content = None if scenario == 'full-flow' else b'\n'.join(SAMPLE.read_bytes().splitlines()[:4]) + b'\n'
                    batch = upload(api, content)
                    result['ocr'] = process_round(api, batch['id'])
                    items = [item for item in result['ocr']['cases'] if item['status'] == 'ocr_done']
                    selected = [items[2], items[0]] if scenario == 'full-flow' else [items[0]]
                    for item in selected:
                        approve(api, item)
                    if scenario == 'full-flow':
                        assert len(result['ocr']['cases']) == 43
                        assert api.post(f"/api/cases/{items[3]['id']}/review", json={'stage': 1, 'decision': 'reject'}).status_code == 200
                        assert api.post(f"/api/cases/{items[4]['id']}/review", json={'stage': 1, 'decision': 'approve'}).status_code == 200
                        assert api.post(f"/api/cases/{items[4]['id']}/review", json={'stage': 2, 'decision': 'reject'}).status_code == 200
                        assert api.post(f"/api/cases/{items[5]['id']}/review", json={'stage': 1, 'decision': 'approve'}).status_code == 200
                    initial = create_export(api, batch['id'], len(selected))
                    result['initial_export'] = initial
                    original_bytes = verify_export(api, initial, selected, directory / 'first.csv')
                    if scenario == 'sigkill':
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            records = vendor.get('/debug/deliveries').json()
                            if records:
                                break
                            time.sleep(0.005)
                        else:
                            raise AssertionError('No upstream ingestion before crash')
                        before = api.get(f"/api/exports/{initial['id']}").json()
                        assert before['status'] == 'delivering' and before['receipt'] is None, before
                        process.kill()
                        process.wait(timeout=5)
                        result['crash'] = {'signal': 'SIGKILL', 'returncode': process.returncode,
                                           'at_epoch': time.time(), 'local_before': before, 'upstream_before': records}
                    else:
                        delivered = wait_delivered(api, initial['id'])
                        result['exports'] = [delivered]
                        assert verify_export(api, delivered, selected, directory / 'delivered.csv') == original_bytes
                        if scenario == 'lost-response':
                            assert delivered['attempts'] >= 2, delivered
                            assert delivered['receipt']['attempts'] >= 2
                        create_export(api, batch['id'], 0)
                        if scenario == 'full-flow':
                            approve(api, items[1])
                            second = create_export(api, batch['id'], 1)
                            second = wait_delivered(api, second['id'])
                            assert second['idempotency_key'] != delivered['idempotency_key']
                            assert second['id'] != delivered['id']
                            verify_export(api, second, [items[1]], directory / 'second.csv')
                            result['exports'].append(second)
                            listed = api.get(f"/api/batches/{batch['id']}/exports").json()
                            assert {item['id'] for item in listed} == {delivered['id'], second['id']}
                            result['final_cases'] = cases(api, batch['id'])
                            exported = {item['id'] for item in result['final_cases'] if item['status'] == 'exported'}
                            assert exported == {item['id'] for item in selected + [items[1]]}
                if scenario == 'sigkill':
                    with service('api', directory / 'restart', database=database,
                                 ocr_url=str(ocr.base_url).rstrip('/') + '/ocr/parse', config=api_config) as (api, _):
                        trace_api(api, result)
                        # Startup alone must recover the persisted operation; no deliver call.
                        delivered = wait_delivered(api, initial['id'])
                        result['exports'] = [delivered]
                        assert delivered['receipt']['attempts'] >= 2
                        assert delivered['attempts'] >= 2
                        assert verify_export(api, delivered, selected, directory / 'recovered.csv') == original_bytes
                        result['final_cases'] = cases(api, batch['id'])
                        assert next(item for item in result['final_cases'] if item['id'] == selected[0]['id'])['status'] == 'exported'
                        create_export(api, batch['id'], 0)
                result['deliveries'] = vendor.get('/debug/deliveries').json()
                verify_ingestions(result['deliveries'], result['exports'])
                if scenario in ('lost-response', 'sigkill'):
                    assert result['deliveries'][0]['attempts'] >= 2
                result['pass'] = True
            finally:
                result['deliveries'] = vendor.get('/debug/deliveries').json()
                result['ocr_requests'] = ocr.get('/debug/requests').json()
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        result['elapsed_seconds'] = time.monotonic() - started
        save(directory / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'scenarios': [], 'pass': False}
    for name in ('full-flow', 'lost-response', 'sigkill'):
        result = run_scenario(args.output / name, name)
        report['scenarios'].append(result)
        save(args.output / 'report.json', report)
        print(f'{name}: {"PASS" if result["pass"] else "FAIL"}; '
              f'ingestions={len(result.get("deliveries", []))}; {result.get("error", "")}', flush=True)
    report['pass'] = all(result['pass'] for result in report['scenarios'])
    save(args.output / 'report.json', report)
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
