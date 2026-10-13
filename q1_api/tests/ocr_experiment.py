"""Native OCR evidence, using the unchanged vendor mock and disposable API DBs.

Run manually: .venv/bin/python q1_api/tests/ocr_experiment.py --output PATH
Every seed/budget gets a fresh mock. Recovery keeps its mock alive across SIGKILL.
JSON evidence is written even on failure; a failed experiment exits nonzero.
"""
from collections import Counter
from contextlib import contextmanager
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx


ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / 'data/account_applications.csv'
DEFAULT_MOCK = {'OCR_FAIL_RATE': '0.2', 'OCR_HANG_RATE': '0.1', 'OCR_HANG_SECONDS': '600'}
DEFAULT_API = {'OCR_TIMEOUT_SECONDS': '1', 'OCR_BACKOFF_SECONDS': '0.1',
               'OCR_BACKOFF_CAP_SECONDS': '0.5'}
RECOVERY_ROUNDS = 3


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


@contextmanager
def service(kind, directory, *, database=None, ocr_url=None, seed=42, attempts=6, config=None):
    """Own processes, ports and logs; expose Popen so tests really kill the API."""
    directory.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        if kind == 'api':
            environment.update(DEFAULT_API)
            environment.update(DATABASE_PATH=str(database), OCR_URL=ocr_url,
                               OCR_MAX_ATTEMPTS=str(attempts))
            command = [sys.executable, '-m', 'uvicorn', 'app.main:app', '--app-dir', 'q1_api',
                       '--fd', str(listener.fileno())]
            descriptors = (listener.fileno(),)
            health = '/api/health'
        else:
            environment.update(DEFAULT_MOCK)
            command = [sys.executable, '-u', 'mock_ocr/server.py', '--port', str(port),
                       '--seed', str(seed)]
            listener.close()  # The unchanged mock's CLI accepts a port, not an inherited FD.
            descriptors = ()
            health = '/online'
        environment.update(config or {})
        log_path = directory / f'{kind}.log'
        with log_path.open('w') as log:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log,
                                       stderr=log, pass_fds=descriptors)
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=2,
                                  trust_env=False) as client:
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise AssertionError(log_path.read_text())
                        try:
                            if client.get(health).status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.02)
                    else:
                        raise AssertionError(f'{kind} startup timed out: {log_path.read_text()}')
                    yield client, process
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)


def upload(client, content=None):
    response = client.post('/api/batches', files={
        'file': ('applications.csv', content if content is not None else SAMPLE.read_bytes(), 'text/csv')})
    assert response.status_code == 201, response.text
    return response.json()


def cases(client, batch_id):
    response = client.get(f'/api/batches/{batch_id}/cases?page_size=100')
    response.raise_for_status()
    page = response.json()
    assert page['total'] == len(page['items']), page
    return page['items']


def process_round(client, batch_id, deadline_seconds=180):
    started = time.monotonic()
    response = client.post(f'/api/batches/{batch_id}/process')
    assert response.status_code == 202, response.text
    trigger_seconds = time.monotonic() - started
    while time.monotonic() - started < deadline_seconds:
        batch = client.get(f'/api/batches/{batch_id}').json()
        if batch['ocr_run']['status'] != 'running':
            return {'trigger_seconds': trigger_seconds, 'elapsed_seconds': time.monotonic() - started,
                    'batch': batch, 'cases': cases(client, batch_id)}
        time.sleep(0.02)
    raise AssertionError(f'OCR exceeded {deadline_seconds}s; last batch: {batch}')


def complete(items):
    return bool(items) and all(
        item['status'] == ('ocr_failed' if 'badscan' in item['image_ref'].lower() else 'ocr_done')
        and not item['ocr_retryable'] for item in items)


def comparison(items):
    return {item['case_ref']: {'status': item['status'], 'ocr_fields': item['ocr_fields']}
            for item in items}


def summarize(requests, items):
    readable = [record for record in requests if record['outcome'] != 'unreadable']
    counts = Counter(record['outcome'] for record in readable)
    return {'all_attempts': len(requests), 'readable_attempts': len(readable),
            'readable_outcomes': dict(counts),
            'readable_outcome_rates': {key: counts[key] / len(readable) if readable else None
                                       for key in ('ok', 'error_500', 'hang')},
            'unreadable_attempts': len(requests) - len(readable),
            'terminal_counts': dict(Counter(item['status'] for item in items)),
            'retryable_exhausted': sum(item['ocr_retryable'] for item in items)}


def measure(directory, seed, attempts):
    """One bounded round; exhaustion remains in evidence and is not hidden by reruns."""
    result = {'seed': seed, 'max_attempts': attempts, 'mock_config': DEFAULT_MOCK,
              'api_config': DEFAULT_API, 'pass': False}
    try:
        with service('mock', directory, seed=seed) as (mock, _):
            try:
                with service('api', directory, database=directory / 'db.sqlite3',
                             ocr_url=str(mock.base_url).rstrip('/') + '/ocr/parse', attempts=attempts) as (api, _):
                    batch = upload(api)
                    result.update(process_round(api, batch['id']))
                    result['pass'] = complete(result['cases'])
            finally:
                result['requests'] = mock.get('/debug/requests').json()
                result['summary'] = summarize(result['requests'], result.get('cases', []))
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        save(directory / 'result.json', result)
    return result


def recover(directory, seed, *, interrupted, content=None, mock_config=None, threshold=5):
    """At most three normal processing rounds after the declared crash boundary."""
    directory.mkdir(parents=True, exist_ok=True)
    result = {'seed': seed, 'interrupted': interrupted, 'max_recovery_rounds': RECOVERY_ROUNDS,
              'mock_config': {**DEFAULT_MOCK, **(mock_config or {})}, 'api_config': DEFAULT_API,
              'max_attempts': 6, 'rounds': [], 'pass': False}
    started = time.monotonic()
    database = directory / 'db.sqlite3'
    try:
        with service('mock', directory, seed=seed, config=mock_config) as (mock, _):
            try:
                with service('api', directory / 'first', database=database,
                             ocr_url=str(mock.base_url).rstrip('/') + '/ocr/parse') as (api, process):
                    batch = upload(api, content)
                    if interrupted:
                        response = api.post(f"/api/batches/{batch['id']}/process")
                        assert response.status_code == 202, response.text
                        deadline = time.monotonic() + 45
                        while time.monotonic() < deadline:
                            requests = mock.get('/debug/requests').json()
                            if len(requests) >= threshold and requests[-1]['outcome'] == 'hang':
                                break
                            time.sleep(0.005)
                        else:
                            raise AssertionError('No delayed in-flight request reached crash threshold')
                        process.kill()
                        process.wait(timeout=5)
                        result['crash'] = {'signal': 'SIGKILL', 'returncode': process.returncode,
                                           'threshold': threshold, 'requests': requests}
                    else:
                        for _ in range(RECOVERY_ROUNDS):
                            result['rounds'].append(process_round(api, batch['id']))
                            if complete(result['rounds'][-1]['cases']):
                                break
                if interrupted:
                    with service('api', directory / 'restart', database=database,
                                 ocr_url=str(mock.base_url).rstrip('/') + '/ocr/parse') as (api, _):
                        result['after_restart'] = api.get(f"/api/batches/{batch['id']}").json()
                        for _ in range(RECOVERY_ROUNDS):
                            result['rounds'].append(process_round(api, batch['id']))
                            if complete(result['rounds'][-1]['cases']):
                                break
                result['cases'] = result['rounds'][-1]['cases']
                result['pass'] = complete(result['cases'])
            finally:
                result['requests'] = mock.get('/debug/requests').json()
                result['summary'] = summarize(result['requests'], result.get('cases', []))
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        result['elapsed_seconds'] = time.monotonic() - started
        save(directory / 'result.json', result)
    return result


def latency_probe(directory, seed=42, requests=20):
    """Separate direct-vendor sample: client latency includes a 1s timeout per attempt."""
    records = []
    with service('mock', directory, seed=seed) as (mock, _):
        for index in range(requests):
            started = time.monotonic()
            record = {'request': index + 1}
            try:
                response = mock.post('/ocr/parse', json={'case_id': f'probe-{index}',
                                     'image_ref': 'id_scan_0001.jpg'}, timeout=1)
                record.update(status_code=response.status_code, response=response.json())
            except httpx.TimeoutException:
                record['timeout'] = True
            record['elapsed_seconds'] = time.monotonic() - started
            records.append(record)
        result = {'seed': seed, 'mock_config': DEFAULT_MOCK, 'timeout_seconds': 1,
                  'client_attempts': records, 'requests': mock.get('/debug/requests').json()}
    save(directory / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seeds', type=int, nargs='+', default=[42, 7, 99])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'measurements': [], 'recoveries': [], 'pass': False}
    save(args.output / 'report.json', report)
    latency_probe(args.output / 'latency')
    for seed in args.seeds:
        for attempts in (4, 6):
            result = measure(args.output / f'seed-{seed}-attempts-{attempts}', seed, attempts)
            report['measurements'].append(result)
            save(args.output / 'report.json', report)
            print(f'seed={seed} attempts={attempts}: {result.get("summary", result.get("error"))}', flush=True)
        clean = recover(args.output / f'seed-{seed}-clean', seed, interrupted=False)
        crashed = recover(args.output / f'seed-{seed}-interrupted', seed, interrupted=True)
        equivalent = (clean['pass'] and crashed['pass'] and
                      comparison(clean['cases']) == comparison(crashed['cases']))
        report['recoveries'].append({'seed': seed, 'pass': equivalent, 'clean': clean,
                                     'interrupted': crashed})
        save(args.output / 'report.json', report)
        print(f'seed={seed} recovery comparison: {"PASS" if equivalent else "FAIL"}', flush=True)
    # A measured single-round exhaustion is a result, not a recovery acceptance failure.
    report['pass'] = (all('error' not in item for item in report['measurements']) and
                      all(item['pass'] for item in report['recoveries']))
    save(args.output / 'report.json', report)
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
