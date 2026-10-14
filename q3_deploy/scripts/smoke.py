#!/usr/bin/env python3
"""Verify one new batch through nginx, retaining all data and mock receipts."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID, uuid4


class SmokeFailure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


class HTTP:
    def __init__(self, base, timeout):
        self.base, self.timeout = base.rstrip('/'), timeout
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, *, method='GET', expected=200, data=None, headers=None,
                raw=False, timeout=None):
        url = path if path.startswith(('http://', 'https://')) else self.base + path
        if isinstance(data, dict):
            data, headers = json.dumps(data).encode(), {'Content-Type': 'application/json'}
        request = Request(url, data=data, headers=headers or {}, method=method)
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as response:
                body = response.read()
                require(response.status == expected,
                        f'{method} {path}: expected HTTP {expected}, got HTTP {response.status}')
        except HTTPError as exc:
            raise SmokeFailure(f'{method} {path}: HTTP {exc.code}: '
                               f'{exc.read(300).decode(errors="replace")}') from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SmokeFailure(f'{method} {path}: request failed: {exc}') from exc
        if raw:
            return body
        try:
            return json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise SmokeFailure(f'{method} {path}: invalid JSON response') from exc

    def poll(self, path, *, seconds, label, state, success, pending):
        deadline = time.monotonic() + seconds
        last = None
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, f'{label} deadline exceeded; last status={last}')
            result = self.request(path, timeout=min(self.timeout, remaining))
            last = state(result)
            if last == success:
                return result
            require(last in pending, f'{label} ended with status={last}: {result}')
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))


def run(args, summary):
    http = HTTP(args.base, args.request_timeout)
    http.poll_interval = args.poll_interval
    ready = http.request('/api/ready')
    require(ready.get('status') == 'ready', f'API is not ready: {ready}')
    seed_id = ready.get('seed_batch_id')
    require(isinstance(seed_id, str), 'Readiness must report the startup seed_batch_id')
    UUID(seed_id)
    summary['seed_batch_id'] = seed_id
    seed = http.request(f'/api/batches/{seed_id}')
    require(seed['accepted_count'] == 43 and seed['rejected_count'] == 7,
            'Startup seed batch must contain 43 accepted and 7 rejected rows')

    boundary = 'smoke-' + uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="account_applications.csv"\r\nContent-Type: text/csv\r\n\r\n').encode()
    body += args.csv.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    batch = http.request('/api/batches', method='POST', expected=201, data=body,
                         headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    batch_id = summary['batch_id'] = batch['id']
    require(batch_id != seed_id, 'Smoke upload must create a separate batch from the startup seed')
    require(batch['accepted_count'] == 43 and batch['rejected_count'] == 7,
            f'Expected 43 accepted and 7 rejected sample rows: {batch}')
    path = f'/api/batches/{batch_id}'
    http.request(path + '/process', method='POST', expected=202)
    batch = http.poll(path, seconds=args.ocr_timeout, label='OCR run',
                      state=lambda result: (result.get('ocr_run') or {}).get('status'),
                      success='completed', pending={'running'})
    summary['ocr_status_counts'] = batch['status_counts']
    require(batch['status_counts'].get('pending', 0) == 0, 'Completed OCR run left pending cases')
    cases = http.request(path + '/cases?status=ocr_done&page_size=100')['items']
    require(bool(cases), f'No successful OCR case is available: {batch["status_counts"]}')
    case = cases[0]
    fields = case.get('ocr_fields') or {}
    require(all(fields.get(key) not in (None, '') for key in
                ('full_name', 'id_number', 'dob', 'issue_date', 'confidence')),
            f'Case {case["id"]} lacks the five saved OCR fields')
    summary['case_ref'] = case['case_ref']
    for stage in (1, 2):
        reviewed = http.request(f'/api/cases/{case["id"]}/review', method='POST',
                                data={'stage': stage, 'decision': 'approve'})
        require(reviewed['status'] == f'stage{stage}_approved', f'Stage {stage} approval did not persist')

    created = http.request(path + '/export', method='POST', expected=202)
    require(created['count'] == 1 and created['export']['row_count'] == 1,
            'Export must contain exactly the one approved case')
    export_id = summary['export_id'] = created['export']['id']
    export = http.poll(f'/api/exports/{export_id}', seconds=args.delivery_timeout,
                       label='Delivery', state=lambda result: result['status'],
                       success='delivered', pending={'pending', 'delivering'})
    receipt = export.get('receipt') or {}
    require(bool(receipt.get('delivery_id')) and all(receipt.get(key) == export[key]
            for key in ('idempotency_key', 'file_name', 'sha256')), 'Delivery receipt does not match the export')
    content = http.request(f'/api/exports/{export_id}/download', raw=True)
    require(hashlib.sha256(content).hexdigest() == export['sha256'], 'Downloaded CSV SHA-256 does not match export')
    rows = list(csv.DictReader(io.StringIO(content.decode('utf-8'), newline='')))
    expected = {key: case[key] for key in ('case_ref', 'applicant_name', 'id_number', 'dob', 'issue_date', 'image_ref')}
    expected.update({'ocr_' + key: str(value) for key, value in fields.items()})
    require(rows == [expected], 'Downloaded CSV does not contain exactly the approved case and saved OCR fields')
    require(http.request(path + '/export', method='POST') == {'count': 0, 'export': None},
            'Second export must be empty; previously exported cases must not be reused')

    destination = urlsplit(args.delivery_url)
    require(destination.scheme in {'http', 'https'} and bool(destination.netloc),
            'DELIVERY_URL must be an absolute HTTP(S) endpoint')
    debug_url = urlunsplit((destination.scheme, destination.netloc, '/debug/deliveries', '', ''))
    deliveries = http.request(debug_url)
    require(isinstance(deliveries, list), 'Invalid downstream debug delivery list')
    matching = [item for item in deliveries if item.get('idempotency_key') == export['idempotency_key']]
    require(len(matching) == 1, f'Expected exactly one ingestion for this key; found {len(matching)}')
    require(all(matching[0].get(key) == receipt.get(key) for key in
                ('delivery_id', 'idempotency_key', 'file_name', 'sha256')),
            'The downstream ingestion does not match the saved receipt and CSV hash')
    summary.update(sha256=export['sha256'], receipt=receipt, **{'pass': True})
    return summary


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be a positive finite number of seconds')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://nginx')
    parser.add_argument('--csv', type=Path, default=Path('/srv/data/account_applications.csv'))
    parser.add_argument('--delivery-url', default=os.environ.get('DELIVERY_URL', 'http://mock_onboarding:9200/ingest'))
    parser.add_argument('--request-timeout', type=positive_seconds, default=5)
    parser.add_argument('--ocr-timeout', type=positive_seconds, default=400)
    parser.add_argument('--delivery-timeout', type=positive_seconds, default=30)
    parser.add_argument('--poll-interval', type=positive_seconds, default=0.5)
    args = parser.parse_args()
    summary = {'pass': False}
    try:
        run(args, summary)
    except (SmokeFailure, OSError, ValueError, KeyError, TypeError) as exc:
        summary['error'] = str(exc)
        print(json.dumps(summary, separators=(',', ':')), file=sys.stderr)
        return 1
    print(json.dumps(summary, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
