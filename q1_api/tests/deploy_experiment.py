"""Manual, isolated Q3 deployment acceptance (never collected by pytest).

Run: .venv/bin/python q1_api/tests/deploy_experiment.py --output NEWDIR
Uses only a generated Compose project and disposable volume. Keeps source and
command evidence even on failure; never operates on another Compose project.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import traceback
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
TABLES = ('batches', 'cases', 'reviews', 'ocr_runs', 'ocr_attempts', 'exports',
          'export_cases', 'delivery_attempts', 'seed_imports')
SOURCE_PREFIXES = ('q1_api/app/', 'q1_api/tests/', 'q3_deploy/', 'mock_ocr/',
                   'mock_onboarding/', 'data/')


def require(value, message):
    if not value:
        raise AssertionError(message)


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def manifest(root, excluded=None):
    result = {}
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = sorted(name for name in subdirectories
                                   if name not in {'evidence', '.git', '.venv', '.gekko-setup', '__pycache__', '.pytest_cache'}
                                   and not (Path(directory) / name).is_symlink()
                                   and not (excluded and (Path(directory) / name).resolve().is_relative_to(excluded)))
        for name in sorted(files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if name == '.DS_Store' or (name.startswith('.env') and name != '.env.example') or name.endswith(
                    ('.db', '.sqlite', '.sqlite3', '.log', '.pyc', '.pyo', '.pyd', '-journal', '-wal', '-shm')):
                continue
            if not path.is_symlink() and name != '.env' and (
                    relative.startswith(SOURCE_PREFIXES) or relative in {'requirements.txt', '.dockerignore'}):
                result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class Experiment:
    def __init__(self, args):
        self.output = args.output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        token = uuid4().hex[:12]
        self.project = 'gekko-check-' + token
        self.holder = self.project + '-ip-holder'
        self.source = ROOT / '.gekko-setup' / ('q3-' + token) / 'source'
        self.compose_file = self.source / 'q3_deploy/docker-compose.yml'
        self.context = args.context
        self.env = dict(os.environ)
        # Shell overrides must not alter the isolated .env baseline.
        for key in list(self.env):
            if key.startswith(('OCR_', 'ONBOARDING_', 'DELIVERY_', 'COMPOSE_', 'BACKUP_')) or key in {
                    'DATABASE_PATH', 'SEED_CSV_PATH', 'NGINX_PORT', 'DOCKER_HOST', 'BASE'}:
                self.env.pop(key)
        self.env.update(DOCKER_CONTEXT=self.context, COMPOSE_PROJECT_NAME=self.project,
                        COMPOSE_FILE=str(self.compose_file))
        self.report = {'pass': False, 'project': self.project, 'context': self.context,
                       'source_snapshot': str(self.source), 'commands': [], 'http': [],
                       'stages': [], 'cleanup_errors': []}
        self.started = time.monotonic()
        self.owned = False
        self.holder_created = False
        self.modes = {}
        self.env_original = None
        self.client = None
        self.flush()

    def flush(self):
        self.report['elapsed_seconds'] = round(time.monotonic() - self.started, 3)
        save(self.output / 'report.json', self.report)

    @contextmanager
    def stage(self, name):
        item = {'name': name, 'pass': False}
        self.report['stages'].append(item)
        started = time.monotonic()
        print(f'[{name}]', flush=True)
        self.flush()
        try:
            yield item
            item['pass'] = True
        except BaseException as exc:
            item['error'] = repr(exc)
            raise
        finally:
            item['elapsed_seconds'] = round(time.monotonic() - started, 3)
            self.flush()

    def command(self, args, *, timeout=30, check=True, cwd=None):
        item = {'args': args, 'cwd': str(cwd or ROOT), 'timeout_seconds': timeout}
        self.report['commands'].append(item)
        command_number = len(self.report['commands'])
        started = time.monotonic()
        try:
            completed = subprocess.run(args, cwd=cwd or ROOT, env=self.env,
                                       text=True, capture_output=True, timeout=timeout)
            item.update(returncode=completed.returncode, stdout=completed.stdout,
                        stderr=completed.stderr)
            if check:
                require(completed.returncode == 0,
                        f'Command failed ({completed.returncode}): {args}: {completed.stderr[-2000:]}')
            return completed.stdout
        except subprocess.TimeoutExpired as exc:
            item.update(error='command_timeout', stdout=self.as_text(exc.stdout),
                        stderr=self.as_text(exc.stderr))
            raise
        except BaseException as exc:
            item['error'] = repr(exc)
            raise
        finally:
            item['elapsed_seconds'] = round(time.monotonic() - started, 3)
            for stream in ('stdout', 'stderr'):
                filename = f'command-{command_number:03d}.{stream}.txt'
                (self.output / filename).write_text(item.get(stream, ''))
                item[stream + '_file'] = filename
            self.flush()

    @staticmethod
    def as_text(value):
        return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')

    def docker(self, *args, **kwargs):
        return self.command(['docker', '--context', self.context, *args], **kwargs)

    def compose(self, *args, **kwargs):
        return self.docker('compose', '-f', str(self.compose_file), '--env-file',
                           str(self.source / 'q3_deploy/.env'), '-p', self.project,
                           *args, **kwargs)

    def python(self, script, *, root=False, service='api'):
        options = ['exec', '-T'] + (['--user', '0'] if root else [])
        return self.compose(*options, service, 'python', '-c', script)

    def inspect(self, service):
        identity = self.compose('ps', '-q', service).strip()
        require(bool(identity), f'Missing container: {service}')
        return json.loads(self.docker('inspect', identity))[0]

    def http(self, method, path, **kwargs):
        started = time.monotonic()
        item = {'method': method, 'path': path}
        self.report['http'].append(item)
        try:
            response = self.client.request(method, path, **kwargs)
            item.update(status=response.status_code, body=response.text[:2000],
                        content_type=response.headers.get('content-type'))
            return response
        except BaseException as exc:
            item['error'] = repr(exc)
            raise
        finally:
            item['elapsed_seconds'] = round(time.monotonic() - started, 3)
            self.flush()

    def wait_ready(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response = self.http('GET', '/api/ready', timeout=2)
                if response.status_code == 200:
                    return response.json()
            except httpx.TransportError:
                pass
            time.sleep(0.5)
        raise AssertionError('Readiness deadline exceeded')

    def wait_health(self, expected, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.inspect('api')['State']['Health']['Status']
            if state == expected:
                return
            time.sleep(1)
        raise AssertionError(f'API health did not become {expected}; last={state}')

    def snapshot(self, name):
        script = '''import json, os, sqlite3
from pathlib import Path
con = sqlite3.connect(Path(os.environ['DATABASE_PATH']).as_uri() + '?mode=ro', uri=True)
con.row_factory = sqlite3.Row
con.execute('BEGIN')
result = {}
for table in TABLE_NAMES:
    rows = [{k: ({'blob_hex': v.hex()} if isinstance(v, bytes) else v) for k,v in dict(row).items()}
            for row in con.execute('SELECT * FROM "' + table + '"')]
    result[table] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
con.rollback()
con.close()
print(json.dumps(result, sort_keys=True))
'''.replace('TABLE_NAMES', repr(TABLES))
        result = json.loads(self.python(script))
        save(self.output / (name + '.json'), result)
        return result

    def prepare(self):
        with self.stage('prepare_source') as item:
            self.report['baseline_head'] = self.command(['git', 'rev-parse', 'HEAD']).strip()
            self.report['git_status'] = self.command(['git', 'status', '--short'])
            self.report['snapshot_is_uncommitted'] = bool(self.report['git_status'].strip())
            files = self.command(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'])
            self.source.mkdir(parents=True)
            for name in sorted(set(files.split('\0')) - {''}):
                relative = Path(name)
                source = ROOT / relative
                if 'evidence' in relative.parts or source.resolve().is_relative_to(self.output):
                    continue
                if not source.exists():
                    continue  # tracked deletion is part of the working snapshot
                require(not source.is_symlink(), f'Refusing symlink in source snapshot: {name}')
                destination = self.source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            save(self.output / 'source-before.json', manifest(ROOT, self.output))
            save(self.output / 'snapshot-before.json', manifest(self.source))
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            configuration = (self.source / 'q3_deploy/.env.example').read_text()
            configuration = '\n'.join(f'NGINX_PORT={port}' if line.startswith('NGINX_PORT=') else line
                                      for line in configuration.splitlines()) + '\n'
            self.env_original = configuration
            (self.source / 'q3_deploy/.env').write_text(configuration)
            self.report['port'] = port
            self.client = httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=10,
                                       trust_env=False)
            # No operation below can match an existing project's name.
            require(not self.docker('ps', '-aq', '--filter', f'label=com.docker.compose.project={self.project}').strip(),
                    'Generated project name already exists')
            require(not self.docker('volume', 'ls', '-q', '--filter', f'label=com.docker.compose.project={self.project}').strip(),
                    'Generated project volume already exists')
            require(not self.docker('network', 'ls', '-q', '--filter', f'name=^{self.project}_default$').strip(),
                    'Generated project network already exists')
            item['copied_files'] = len(list(self.source.rglob('*')))

    def run(self):
        self.prepare()
        with self.stage('fresh_start') as item:
            self.owned = True
            logs = self.compose('up', '--build', '-d', '--wait', '--wait-timeout', '180', timeout=240)
            (self.output / 'build.stdout.txt').write_text(logs)
            (self.output / 'build.stderr.txt').write_text(self.report['commands'][-1]['stderr'])
            containers = {service: self.inspect(service) for service in ('api', 'mock_ocr', 'mock_onboarding', 'nginx')}
            save(self.output / 'fresh-containers.json', containers)
            for service, details in containers.items():
                require(details['State']['Health']['Status'] == 'healthy', f'{service} is not healthy')
                bindings = details['HostConfig'].get('PortBindings') or {}
                if service == 'nginx':
                    require(bindings == {'80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(self.report['port'])}]},
                            f'Unexpected published nginx ports: {bindings}')
                else:
                    require(not bindings, f'{service} unexpectedly publishes ports')
            require(self.python('import os; print(os.getuid())').strip() == '10001', 'API UID must be 10001')
            ready = self.wait_ready()
            self.seed = ready['seed_batch_id']
            seed = self.http('GET', f'/api/batches/{self.seed}').json()
            require((seed['accepted_count'], seed['rejected_count']) == (43, 7), 'Incorrect seed counts')
            item['seed_batch_id'] = self.seed

        with self.stage('real_proxy_routes'):
            response = self.http('POST', '/ocr/parse', json={'case_id': 'route-probe', 'image_ref': 'route-probe.png'})
            require(response.status_code == 200 and response.json()['id_number'].startswith('TEST-ID-'),
                    'Original /ocr/parse path did not reach the mock')
            response = self.http('GET', f'/api/batches/{self.seed}/cases?page=2&page_size=3')
            page = response.json()
            require(response.status_code == 200 and page['page'] == 2 and page['page_size'] == 3
                    and len(page['items']) == 3 and page['total'] == 43, 'API query string was not preserved')

        with self.stage('smoke_default_faults'):
            stdout = self.command(['bash', 'scripts/smoke_test.sh'], cwd=self.source / 'q3_deploy', timeout=480)
            (self.output / 'smoke.stdout.txt').write_text(stdout)
            self.smoke = json.loads(stdout.strip().splitlines()[-1])
            save(self.output / 'smoke.json', self.smoke)
            require(self.smoke['pass'] and self.smoke['batch_id'] and self.smoke['export_id'], 'Smoke did not pass')
            self.baseline = self.snapshot('business-before')

        with self.stage('force_new_api_ip') as item:
            before = self.inspect('api')
            nginx_id = self.inspect('nginx')['Id']
            network = self.project + '_default'
            old_ip = before['NetworkSettings']['Networks'][network]['IPAddress']
            self.compose('stop', 'api')
            self.compose('rm', '-f', 'api')
            self.docker('run', '-d', '--name', self.holder, '--label', f'gekko.acceptance.project={self.project}',
                        '--network', network, '--ip', old_ip, before['Image'], 'python', '-c', 'import time; time.sleep(120)')
            self.holder_created = True
            self.compose('up', '-d', '--no-deps', 'api')
            require(self.wait_ready()['seed_batch_id'] == self.seed, 'Seed changed after API replacement')
            after = self.inspect('api')
            new_ip = after['NetworkSettings']['Networks'][network]['IPAddress']
            require(old_ip != new_ip and before['Id'] != after['Id'], 'API did not receive a new container and IP')
            require(self.inspect('nginx')['Id'] == nginx_id, 'Nginx was restarted')
            require(self.snapshot('business-after-api-replacement') == self.baseline, 'Business rows changed after API replacement')
            item.update(old_ip=old_ip, new_ip=new_ip, nginx_id=nginx_id)
            self.docker('rm', '-f', self.holder)
            self.holder_created = False

        with self.stage('full_restart_preserves_local_data') as item:
            self.compose('down', timeout=60)
            self.compose('up', '-d', '--wait', '--wait-timeout', '90', timeout=120)
            require(self.wait_ready()['seed_batch_id'] == self.seed, 'Restart duplicated/replaced seed')
            require(self.snapshot('business-after-full-restart') == self.baseline, 'Business rows changed after full restart')
            export = self.http('GET', '/api/exports/' + self.smoke['export_id']).json()
            require(export['status'] == 'delivered', 'Previously delivered export lost its state')
            deliveries = json.loads(self.python("import urllib.request; print(urllib.request.urlopen('http://mock_onboarding:9200/debug/deliveries', timeout=3).read().decode())"))
            require(deliveries == [], 'Previously delivered export was resent after full restart')
            item['limitation'] = 'Mock remote receipts/idempotency registry reset on restart; this proves local persistence and no resend only.'

        with self.stage('multipart_and_proxy_body_limits'):
            for size, expected, code in ((1024 * 1024 + 1, 400, 'invalid_csv'),
                                         (2 * 1024 * 1024 + 1, 413, 'upload_too_large')):
                response = self.http('POST', '/api/batches', files={'file': ('bad.csv', b'x' * size, 'text/csv')})
                require(response.status_code == expected and response.json()['detail']['code'] == code,
                        f'Wrong API body limit result for {size} bytes')
            response = self.http('POST', '/api/batches', content=b'x' * (3 * 1024 * 1024 + 1),
                                 headers={'Content-Type': 'application/octet-stream'})
            require(response.status_code == 413 and 'text/html' in response.headers.get('content-type', ''),
                    'Nginx did not enforce 3 MiB body limit')
            require(self.snapshot('business-after-body-limits') == self.baseline, 'Rejected uploads changed business rows')

        with self.stage('database_fault_readiness'):
            for target, mode in (('/data/app.db', 0o444), ('/data', 0o555)):
                original = int(self.python(f'import os, stat; print(stat.S_IMODE(os.stat({target!r}).st_mode))', root=True))
                self.modes[target] = original
                try:
                    self.python(f'import os; os.chmod({target!r}, {mode})', root=True)
                    require(self.http('GET', '/api/ready').status_code == 503, f'{target}: ready should be 503')
                    require(self.http('GET', '/api/health').status_code == 200, f'{target}: live should remain 200')
                    if target.endswith('.db'):
                        self.wait_health('unhealthy')
                finally:
                    self.python(f'import os; os.chmod({target!r}, {original})', root=True)
                    self.modes.pop(target)
                self.wait_ready()
                self.wait_health('healthy')
            require(self.snapshot('business-after-readiness-faults') == self.baseline, 'Readiness faults changed business rows')

        with self.stage('bounded_direct_ocr_hang') as item:
            config = self.env_original
            for name, value in {'OCR_FAIL_RATE': '0', 'OCR_HANG_RATE': '1', 'OCR_HANG_SECONDS': '8'}.items():
                config = '\n'.join(f'{name}={value}' if line.startswith(name + '=') else line
                                   for line in config.splitlines()) + '\n'
            try:
                (self.source / 'q3_deploy/.env').write_text(config)
                self.compose('up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '30', 'mock_ocr', timeout=60)
                # nginx caches Docker DNS for at most 5s; recreating may keep or change the address.
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    check = self.http('POST', '/ocr/parse', json={})
                    if check.status_code == 400:
                        break
                    time.sleep(.5)
                require(check.status_code == 400, 'New OCR mock was not reachable through nginx')
                started = time.monotonic()
                response = self.http('POST', '/ocr/parse', json={'case_id': 'hang-probe', 'image_ref': 'hang.png'})
                elapsed = time.monotonic() - started
                require(response.status_code == 504 and 4 <= elapsed <= 7.5,
                        f'Expected a bounded ~5s gateway timeout, got {response.status_code} in {elapsed:.2f}s')
                requests = json.loads(self.python("import urllib.request; print(urllib.request.urlopen('http://mock_ocr:9100/debug/requests', timeout=3).read().decode())"))
                require(len(requests) == 1 and requests[0]['outcome'] == 'hang' and requests[0]['case_id'] == 'hang-probe',
                        'Gateway retried POST or mock did not receive the expected one hang')
                save(self.output / 'ocr-hang-requests.json', requests)
                item['gateway_elapsed_seconds'] = round(elapsed, 3)
            finally:
                (self.source / 'q3_deploy/.env').write_text(self.env_original)
                self.compose('up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '30', 'mock_ocr', timeout=60)
        self.report['pass'] = True

    def cleanup(self):
        print('[cleanup_owned_project]', flush=True)
        def attempt(label, action):
            try:
                action()
            except BaseException as exc:
                self.report['cleanup_errors'].append({'action': label, 'error': repr(exc)})
        for target, mode in self.modes.items():
            attempt('restore_permissions_' + target,
                    lambda target=target, mode=mode: self.python(f'import os; os.chmod({target!r}, {mode})', root=True))
        if self.env_original is not None:
            attempt('restore_snapshot_env', lambda: (self.source / 'q3_deploy/.env').write_text(self.env_original))
        if self.owned:
            attempt('capture_logs', lambda: (self.output / 'compose.logs.txt').write_text(
                self.compose('logs', '--no-color', '--timestamps', timeout=30)))
            # Remove by our generated name even if docker run succeeded just before interruption.
            attempt('remove_ip_holder', lambda: self.docker('rm', '-f', self.holder, check=False))
            attempt('down_owned_project', lambda: self.compose('down', '-v', '--remove-orphans', timeout=90))
        attempt('source_after', lambda: save(self.output / 'source-after.json', manifest(ROOT, self.output)))
        source_before = self.output / 'source-before.json'
        source_after = self.output / 'source-after.json'
        if source_before.exists() and source_after.exists():
            self.report['original_source_unchanged'] = json.loads(source_before.read_text()) == json.loads(source_after.read_text())
        if self.source.exists():
            attempt('snapshot_after', lambda: save(self.output / 'snapshot-after.json', manifest(self.source)))
            before = self.output / 'snapshot-before.json'
            after = self.output / 'snapshot-after.json'
            if before.exists() and after.exists():
                self.report['snapshot_source_unchanged'] = json.loads(before.read_text()) == json.loads(after.read_text())
                if not self.report['snapshot_source_unchanged']:
                    self.report['cleanup_errors'].append({'action': 'source_integrity', 'error': 'Snapshot source changed'})
        if self.client is not None:
            self.client.close()
        if self.report['cleanup_errors']:
            self.report['pass'] = False
        self.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory; must not exist')
    parser.add_argument('--context', default='colima-gekko')
    args = parser.parse_args()
    experiment = Experiment(args)
    try:
        experiment.run()
    except BaseException as exc:
        experiment.report.update(error=repr(exc), traceback=traceback.format_exc(), **{'pass': False})
    finally:
        experiment.cleanup()
    print(json.dumps({'pass': experiment.report['pass'], 'report': str(experiment.output / 'report.json')}))
    return 0 if experiment.report['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
