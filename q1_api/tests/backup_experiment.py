"""Manual isolated Q3 backup/restore acceptance; never collected by pytest.

Run: .venv/bin/python q1_api/tests/backup_experiment.py --context colima-gekko --output NEWDIR
Builds an isolated working-source snapshot and removes only its generated Compose
project/volumes. Raw commands, HTTP evidence, source hashes and reports survive.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import traceback

from deploy_experiment import Experiment, TABLES, require, save


BACKUP_NAME = re.compile(r'^backup-\d{8}T\d{12}Z-[0-9a-f]{8}\.sqlite3$')


class BackupExperiment(Experiment):
    def __init__(self, args):
        super().__init__(args)
        for key in list(self.env):
            if key.startswith('BACKUP_'):
                self.env.pop(key)
        self.api_stopped = False
        self.report['runbook_commands'] = []

    def python(self, script, *, root=False, service='api'):
        if self.api_stopped and service == 'api':
            options = ['run', '--rm', '--no-deps', '-T'] + (['--user', '0'] if root else [])
            return self.compose(*options, service, 'python', '-c', script, timeout=60)
        return super().python(script, root=root, service=service)

    def runbook(self, script, *args, success=True):
        command = ['bash', 'scripts/' + script, *args]
        self.report['runbook_commands'].append({'cwd': str(self.source / 'q3_deploy'), 'args': command})
        stdout = self.command(command, cwd=self.source / 'q3_deploy', timeout=90, check=False)
        result = self.report['commands'][-1]
        if not success:
            require(result['returncode'] != 0, f'{script} unexpectedly succeeded')
            require(bool(result['stderr'].strip()), f'{script} refusal had no diagnostic')
            return result
        require(result['returncode'] == 0, f'{script} failed: {result["stderr"]}')
        return json.loads(stdout.strip().splitlines()[-1])

    def inventory(self, name):
        result = json.loads(self.python('''import hashlib, json, os
from pathlib import Path
result = {}
for directory in (Path(os.environ['DATABASE_PATH']).parent, Path(os.environ['BACKUP_DIR'])):
    result[str(directory)] = [{
        'name': str(path.relative_to(directory)), 'size': path.stat().st_size,
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()
    } for path in sorted(directory.rglob('*')) if path.is_file()]
print(json.dumps(result, sort_keys=True))
'''))
        save(self.output / (name + '.json'), result)
        return result

    @staticmethod
    def snapshot_names(inventory):
        return sorted(item['name'] for item in inventory['/backups'] if BACKUP_NAME.fullmatch(item['name']))

    def ingestions(self, name):
        result = json.loads(self.python("import urllib.request; print(urllib.request.urlopen("
                                        "'http://mock_onboarding:9200/debug/deliveries', timeout=3).read().decode())"))
        require(isinstance(result, list), 'Onboarding debug response is not a list')
        save(self.output / (name + '.json'), result)
        return result

    def run(self):
        self.prepare()
        with self.stage('fresh_isolated_start') as item:
            self.owned = True
            self.compose('up', '--build', '-d', '--wait', '--wait-timeout', '180', timeout=240)
            containers = {name: self.inspect(name) for name in ('api', 'mock_ocr', 'mock_onboarding', 'nginx')}
            save(self.output / 'fresh-containers.json', containers)
            require(all(container['State']['Health']['Status'] == 'healthy' for container in containers.values()),
                    'Fresh services must all be healthy')
            mounts = {mount['Destination']: mount for mount in containers['api']['Mounts']}
            require(all(mounts[path]['Type'] == 'volume' for path in ('/data', '/backups')),
                    'Database and backups require named volumes')
            require(mounts['/data']['Name'] != mounts['/backups']['Name'], 'Backups share the database volume')
            for path in ('/data', '/backups'):
                volume = json.loads(self.docker('volume', 'inspect', mounts[path]['Name']))[0]
                require(volume['Labels']['com.docker.compose.project'] == self.project,
                        'Volume is not owned by this experiment')
            require(self.python('import os; print(os.getuid())').strip() == '10001', 'API must run as UID 10001')
            self.seed = self.wait_ready()['seed_batch_id']
            self.mock_ids = {name: containers[name]['Id'] for name in ('mock_ocr', 'mock_onboarding')}
            item.update(seed_batch_id=self.seed, volumes={path: mounts[path]['Name'] for path in ('/data', '/backups')})
            require(self.snapshot_names(self.inventory('files-initial')) == [], 'Fresh backup volume is not empty')

        with self.stage('real_smoke_with_default_faults') as item:
            stdout = self.command(['bash', 'scripts/smoke_test.sh'], cwd=self.source / 'q3_deploy', timeout=480)
            (self.output / 'smoke.stdout.txt').write_text(stdout)
            self.smoke = json.loads(stdout.strip().splitlines()[-1])
            save(self.output / 'smoke.json', self.smoke)
            require(self.smoke['pass'], 'Real deployment smoke failed')
            self.baseline = self.snapshot('business-before-backup')
            require(set(self.baseline) == set(TABLES) and all(self.baseline.values()),
                    'Smoke must populate all nine business tables')
            export = next(row for row in self.baseline['exports'] if row['id'] == self.smoke['export_id'])
            require(export['status'] == 'delivered' and bool(export['receipt']), 'Missing saved delivered receipt')
            require(bool(export['content']['blob_hex']), 'Missing saved CSV BLOB')
            self.export_path = '/api/exports/' + self.smoke['export_id']
            response = self.http('GET', self.export_path + '/download')
            require(response.status_code == 200, 'Baseline CSV download failed')
            self.csv_bytes = response.content
            (self.output / 'export-before.csv').write_bytes(self.csv_bytes)
            require(hashlib.sha256(self.csv_bytes).hexdigest() == self.smoke['sha256'], 'Baseline CSV hash mismatch')
            self.remote_before = self.ingestions('ingestions-before')
            require(len(self.remote_before) == 1, 'Fresh smoke should have exactly one remote ingestion')
            item['table_counts'] = {table: len(rows) for table, rows in self.baseline.items()}

        with self.stage('runbook_diagnostics'):
            self.compose('ps')
            self.compose('logs', '--tail=100', 'api', 'mock_ocr', 'mock_onboarding', 'nginx')
            self.compose('exec', '-T', 'api', 'id')
            self.compose('exec', '-T', 'api', 'ls', '-ld', '/data', '/backups')
            self.compose('exec', '-T', 'api', 'ls', '-l', '/data')
            online = self.python("import urllib.request; print(urllib.request.urlopen("
                                 "'http://localhost:9100/online', timeout=2).status)", service='mock_ocr')
            require(online.strip() == '200', 'Original OCR /online route is not available')
            response = self.http('POST', '/ocr/parse', json={})
            require(response.status_code == 400, 'Original /ocr/parse proxy did not reach mock validation')
            require(self.http('GET', '/api/health').status_code == 200, 'API liveness diagnostic failed')
            response = self.http('GET', f'/api/batches/{self.smoke["batch_id"]}/cases?status=ocr_failed&page_size=100')
            require(response.status_code == 200, 'Saved OCR failure diagnostic failed')
            response = self.http('GET', f'/api/batches/{self.smoke["batch_id"]}/exports')
            require(response.status_code == 200, 'Saved delivery diagnostic failed')

        with self.stage('nine_online_backups_and_newest_seven_retention') as item:
            created = []
            for index in range(1, 10):
                require(self.inspect('api')['State']['Running'], 'Online backup requires a running API')
                before = self.snapshot_names(self.inventory(f'backup-{index:02d}-files-before'))
                result = self.runbook('backup.sh')
                after = self.snapshot_names(self.inventory(f'backup-{index:02d}-files-after'))
                require(result['operation'] == 'backup' and BACKUP_NAME.fullmatch(result['backup']),
                        'Backup script did not report a valid snapshot basename')
                require(result['backup'] not in created, 'Backup overwrote a previous snapshot')
                created.append(result['backup'])
                require(result['before'] == before and result['after'] == after,
                        'Backup report differs from independently listed files')
                require(after == sorted(created)[-7:], 'Retention must keep exactly the newest seven snapshots')
                require(self.wait_ready()['seed_batch_id'] == self.seed, 'Online backup disrupted readiness or seed')
            self.known_backup = created[-1]
            require(self.snapshot('business-after-online-backups') == self.baseline, 'Online backup changed business rows')
            item.update(created=created, retained=after, restore_target=self.known_backup)

        with self.stage('real_post_backup_mutation_and_running_restore_refusal') as item:
            response = self.http('POST', '/api/batches', files={
                'file': ('post-backup.csv', (self.source / 'data/account_applications.csv').read_bytes(), 'text/csv')})
            require(response.status_code == 201, 'Post-backup HTTP upload failed')
            mutation = response.json()
            self.mutation_id = mutation['id']
            require((mutation['accepted_count'], mutation['rejected_count']) == (43, 7), 'Mutation upload counts differ')
            self.mutated = self.snapshot('business-after-mutation')
            require(self.mutated != self.baseline and any(row['id'] == self.mutation_id for row in self.mutated['batches']),
                    'HTTP upload did not change persisted business rows')
            refusal = self.runbook('restore.sh', self.known_backup, success=False)
            require('running' in refusal['stderr'].lower(), 'Restore was refused for an unexpected reason')
            require(self.snapshot('business-after-running-refusal') == self.mutated, 'Running restore refusal changed rows')
            require(self.wait_ready()['seed_batch_id'] == self.seed, 'Running restore refusal stopped the API')
            item['post_backup_batch_id'] = self.mutation_id

        with self.stage('stopped_api_corrupt_restore_refused_without_database_change') as item:
            corrupt = 'backup-20000101T000000000000Z-00000000.sqlite3'
            self.python("import os; from pathlib import Path; "
                        f"(Path(os.environ['BACKUP_DIR']) / {corrupt!r}).write_bytes(b'not a SQLite database')")
            self.report['runbook_commands'].append({'args': ['docker', 'compose', 'stop', 'api']})
            self.compose('stop', 'api', timeout=60)
            self.api_stopped = True
            before = self.inventory('corrupt-restore-files-before')
            self.runbook('restore.sh', corrupt, success=False)
            after = self.inventory('corrupt-restore-files-after')
            require(before['/data'] == after['/data'], 'Corrupt restore modified database files')
            require(self.snapshot('business-after-corrupt-refusal') == self.mutated, 'Corrupt restore modified rows')
            item['database_files_unchanged'] = True

        with self.stage('restore_known_backup_with_mocks_kept_alive') as item:
            self.inventory('valid-restore-files-before')
            result = self.runbook('restore.sh', self.known_backup)
            require(result['operation'] == 'restore' and result['restored'] == self.known_backup,
                    'Restore did not confirm the selected backup')
            after = self.inventory('valid-restore-files-after')
            require(self.snapshot('business-restored-offline') == self.baseline, 'Offline restore differs from all nine baseline tables')
            require(result.get('safety_backup') and any(row['name'] == result['safety_backup'] for row in after['/backups']),
                    'Restore did not preserve the database it replaced')
            item.update(result)
            self.report['runbook_commands'].append({'args': ['docker', 'compose', 'up', '-d', '--no-deps', 'api']})
            self.compose('up', '-d', '--no-deps', 'api', timeout=60)
            self.api_stopped = False
            require(self.wait_ready()['seed_batch_id'] == self.seed, 'Restore changed the startup seed identity')
            self.wait_health('healthy')
            for name, identity in self.mock_ids.items():
                require(self.inspect(name)['Id'] == identity, f'{name} was recreated during restore')
            require(self.snapshot('business-restored-online') == self.baseline, 'Restart changed restored business rows')
            require(self.http('GET', '/api/batches/' + self.mutation_id).status_code == 404,
                    'Post-backup batch survived rollback')

        with self.stage('restored_export_bytes_receipt_and_no_duplicate_ingestion') as item:
            response = self.http('GET', self.export_path)
            require(response.status_code == 200, 'Restored export lookup failed')
            export = response.json()
            require(export['status'] == 'delivered' and export['receipt'] == self.smoke['receipt'],
                    'Restored export lost delivered status or original receipt')
            response = self.http('GET', self.export_path + '/download')
            require(response.status_code == 200 and response.content == self.csv_bytes, 'Restored CSV bytes differ')
            (self.output / 'export-after.csv').write_bytes(response.content)
            require(hashlib.sha256(response.content).hexdigest() == self.smoke['sha256'] == export['sha256'],
                    'Restored CSV hash differs')
            require(self.ingestions('ingestions-after-restore') == self.remote_before, 'Restore created a duplicate ingestion')
            for unused in range(3):
                response = self.http('POST', self.export_path + '/deliver')
                require(response.status_code == 202 and response.json()['status'] == 'delivered',
                        'Repeat deliver changed the restored delivered state')
            # Give the real dispatcher time to consume its wake event before checking.
            time.sleep(2)
            require(self.ingestions('ingestions-after-repeat-deliver') == self.remote_before,
                    'Repeat deliver caused another remote ingestion')
            require(self.snapshot('business-final') == self.baseline, 'Verification changed restored business rows')
            self.inventory('files-final')
            item.update(sha256=export['sha256'], receipt=export['receipt'], repeat_deliver_requests=3,
                        remote_ingestions=len(self.remote_before), all_nine_tables_equal=True)
        self.report['pass'] = True

    def cleanup(self):
        super().cleanup()
        if self.report.get('original_source_unchanged') is False:
            self.report['cleanup_errors'].append({'action': 'source_integrity', 'error': 'Original source changed'})
            self.report['pass'] = False
        self.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory; must not exist')
    parser.add_argument('--context', default='colima-gekko')
    experiment = BackupExperiment(parser.parse_args())
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
