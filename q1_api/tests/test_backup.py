"""Exercise the operational CLI on real SQLite files, including WAL and locks."""
import fcntl
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time

import pytest

from app.db import Database


SCRIPT = Path(__file__).resolve().parents[2] / 'q3_deploy/scripts/database_backup.py'
NAME = r'backup-\d{8}T\d{12}Z-[0-9a-f]{8}\.sqlite3'


@pytest.fixture
def storage(tmp_path):
    source, backups = tmp_path / 'app.db', tmp_path / 'backups'
    Database(source).initialize()
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE evidence (value TEXT)')
        db.execute("INSERT INTO evidence VALUES ('original')")
    backups.mkdir()
    return source, backups


def run(storage, *arguments, timeout='2'):
    source, backups = storage
    return subprocess.run([sys.executable, str(SCRIPT), *arguments],
                          env={**os.environ, 'DATABASE_PATH': str(source),
                               'BACKUP_DIR': str(backups), 'BACKUP_TIMEOUT_SECONDS': timeout},
                          capture_output=True, text=True, timeout=5)


def success(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def values(path):
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        return db.execute('SELECT value FROM evidence ORDER BY rowid').fetchall()


def test_online_backup_includes_committed_wal_but_not_uncommitted_changes(storage):
    source, backups = storage
    with sqlite3.connect(source) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("INSERT INTO evidence VALUES ('committed WAL')")
        writer.commit()
        writer.execute("INSERT INTO evidence VALUES ('uncommitted')")
        result = success(run(storage, 'backup'))
        assert re.fullmatch(NAME, result['backup'])
        assert result['before'] == []
        assert result['after'] == [result['backup']]
        assert values(backups / result['backup']) == [('original',), ('committed WAL',)]
        writer.rollback()


def test_nine_backups_keep_latest_seven_without_touching_other_files(storage):
    source, backups = storage
    unrelated = backups / 'operator-notes.txt'
    unrelated.write_text('keep me')
    symlink = backups / 'backup-20000101T000000000000Z-00000000.sqlite3'
    symlink.symlink_to(source)
    folder = backups / 'backup-20000102T000000000000Z-00000000.sqlite3'
    folder.mkdir()
    names = []
    for index in range(9):
        result = success(run(storage, 'backup'))
        assert result['before'] == names[-7:]
        names.append(result['backup'])
        assert result['after'] == names[-7:]
    assert len(set(names)) == 9
    assert all(not (backups / name).exists() for name in names[:2])
    assert all((backups / name).is_file() for name in names[2:])
    assert unrelated.read_text() == 'keep me'
    assert symlink.is_symlink() and folder.is_dir()


@pytest.mark.parametrize('clock_behavior', ['rollback', 'frozen'])
def test_clock_rollback_keeps_latest_seven_successful_snapshots(storage, monkeypatch, clock_behavior):
    # Only the wall clock is controlled; snapshots and restore use real SQLite.
    spec = importlib.util.spec_from_file_location('backup_clock_test', SCRIPT)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    source, backups = storage

    class Clock(datetime):
        instant = datetime(2027, 1, 1, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.instant

    monkeypatch.setattr(helper, 'datetime', Clock)
    names = []
    for index in range(16):
        # First fill the directory normally, then repeatedly rewind/freeze time.
        Clock.instant = (datetime(2027, 1, 1, second=index, tzinfo=timezone.utc)
                         if index < 7 else
                         datetime(2026 - index if clock_behavior == 'rollback' else 2027,
                                  1, 1, second=6, tzinfo=timezone.utc))
        with sqlite3.connect(source) as db:
            db.execute('UPDATE evidence SET value=?', (str(index),))
        result = helper.backup(source, backups, time.monotonic() + 2)
        names.append(result['backup'])
        assert (backups / result['backup']).is_file()
        assert result['after'] == names[-7:]
        assert [values(backups / name) for name in result['after']] == [
            [(str(value),)] for value in range(max(0, index - 6), index + 1)]
    helper.restore(source, backups, names[-1], time.monotonic() + 2)
    assert values(source) == [('15',)]


def test_future_dated_legacy_backups_keep_order_across_cli_restarts(storage):
    source, backups = storage
    names = []
    for index in range(7):
        name = f'backup-99980101T00000{index}000000Z-00000000.sqlite3'
        shutil.copyfile(source, backups / name)
        names.append(name)
    for index in range(9):
        with sqlite3.connect(source) as db:
            db.execute('UPDATE evidence SET value=?', (str(index),))
        # Each invocation is a new OS process, so ordering cannot rely on memory.
        result = success(run(storage, 'backup'))
        assert result['before'] == names[-7:]
        names.append(result['backup'])
        assert result['after'] == names[-7:]
        assert values(backups / result['backup']) == [(str(index),)]
    source.write_bytes(b'corrupt')
    assert run(storage, 'backup').returncode != 0
    assert sorted(p.name for p in backups.glob('*.sqlite3')) == names[-7:]
    assert [values(backups / name) for name in names[-7:]] == [[(str(i),)] for i in range(2, 9)]


@pytest.mark.parametrize('timestamp', ['99991231T235959999999Z', '20261301T000000000000Z'])
def test_unusable_timestamp_refuses_without_pruning(storage, timestamp):
    source, backups = storage
    name = f'backup-{timestamp}-00000000.sqlite3'
    shutil.copyfile(source, backups / name)
    result = run(storage, 'backup')
    assert result.returncode != 0
    assert json.loads(result.stderr)['error']
    assert list(backups.glob('*.sqlite3')) == [backups / name]
    assert values(backups / name) == [('original',)]


@pytest.mark.parametrize('damage', ['missing', 'empty', 'corrupt', 'schema', 'column', 'foreign_key'])
def test_invalid_source_never_publishes_or_prunes(storage, damage):
    source, backups = storage
    old = backups / 'backup-20000101T000000000000Z-12345678.sqlite3'
    shutil.copyfile(source, old)
    if damage == 'missing':
        source.unlink()
    elif damage == 'empty':
        source.write_bytes(b'')
    elif damage == 'corrupt':
        source.write_bytes(b'not a SQLite database')
    else:
        with sqlite3.connect(source) as db:
            if damage == 'schema':
                db.execute('DROP TABLE delivery_attempts')
            elif damage == 'column':
                db.execute('ALTER TABLE batches RENAME COLUMN rejected_rows TO wrong_column')
            else:
                db.execute("INSERT INTO export_cases VALUES ('unknown-case', 'unknown-export')")
    result = run(storage, 'backup')
    assert result.returncode != 0
    assert json.loads(result.stderr)['error']
    assert old.exists()
    assert sorted(p.name for p in backups.iterdir() if p.suffix == '.sqlite3') == [old.name]
    assert not list(backups.glob('*.tmp'))
    if damage == 'missing':
        assert not source.exists()


def test_locked_source_finishes_within_deadline_without_publishing(storage):
    source, backups = storage
    with sqlite3.connect(source) as writer:
        writer.execute('BEGIN EXCLUSIVE')
        started = time.monotonic()
        result = run(storage, 'backup', timeout='.15')
        assert result.returncode != 0
        assert time.monotonic() - started < 2
        assert not list(backups.glob('*.sqlite3'))
        assert not list(backups.glob('*.tmp'))


def test_shared_operation_lock_rejects_backup_and_restore(storage):
    _, backups = storage
    name = success(run(storage, 'backup'))['backup']
    with (backups / '.database.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for args in [('backup',), ('restore', name)]:
            result = run(storage, *args)
            assert result.returncode != 0
            assert 'operation' in json.loads(result.stderr)['error'].lower()
    assert values(backups / name) == [('original',)]


def test_restore_replaces_whole_snapshot_and_keeps_current_database_safety_copy(storage):
    source, backups = storage
    name = success(run(storage, 'backup'))['backup']
    with sqlite3.connect(source) as db:
        db.execute("UPDATE evidence SET value='newer state'")
        db.execute('CREATE TABLE after_backup (value TEXT)')
    result = success(run(storage, 'restore', name))
    assert result['restored'] == name
    assert result['safety_backup'].startswith('pre-restore/')
    assert values(backups / result['safety_backup']) == [('newer state',)]
    assert values(source) == [('original',)]
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='after_backup'").fetchall() == []
    assert values(backups / name) == [('original',)]


def test_restore_missing_target_and_discard_stale_wal_sidecars(storage):
    source, backups = storage
    name = success(run(storage, 'backup'))['backup']
    source.unlink()
    Path(str(source) + '-wal').write_bytes(b'stale wal')
    Path(str(source) + '-shm').write_bytes(b'stale shm')
    result = success(run(storage, 'restore', name))
    assert result['safety_backup'] is None
    assert values(source) == [('original',)]
    assert not Path(str(source) + '-wal').exists()
    assert not Path(str(source) + '-shm').exists()


@pytest.mark.parametrize('bad_name', ['../app.db', '/tmp/app.db', 'unknown.sqlite3', 'symlink'])
def test_restore_rejects_untrusted_names_without_changing_target(storage, bad_name):
    source, backups = storage
    if bad_name == 'symlink':
        bad_name = 'backup-20000101T000000000000Z-12345678.sqlite3'
        (backups / bad_name).symlink_to(source)
    before = source.read_bytes()
    result = run(storage, 'restore', bad_name)
    assert result.returncode != 0
    assert source.read_bytes() == before


def test_restore_invalid_snapshot_preserves_target(storage):
    source, backups = storage
    name = 'backup-20000101T000000000000Z-12345678.sqlite3'
    (backups / name).write_bytes(b'not a SQLite database')
    before = source.read_bytes()
    assert run(storage, 'restore', name).returncode != 0
    assert source.read_bytes() == before


def test_restore_corrupt_current_target_refuses_with_quarantine_guidance(storage):
    source, backups = storage
    name = success(run(storage, 'backup'))['backup']
    source.write_bytes(b'corrupted current db')
    result = run(storage, 'restore', name)
    assert result.returncode != 0
    assert 'quarantine' in result.stderr.lower()
    assert source.read_bytes() == b'corrupted current db'


def test_seeded_database_backup_preserves_seed_marker(storage):
    from app.seed import bootstrap_seed
    source, backups = storage
    batch = bootstrap_seed(Database(source), SCRIPT.parents[2] / 'data/account_applications.csv')
    name = success(run(storage, 'backup'))['backup']
    with sqlite3.connect(backups / name) as db:
        assert db.execute('SELECT batch_id FROM seed_imports').fetchall() == [(batch,)]
        assert db.execute('SELECT count(*) FROM cases').fetchone() == (43,)


@pytest.fixture
def shell(storage, tmp_path):
    # Docker is the external boundary. The stand-in actually invokes our CLI and
    # rejects a wrong Compose directory or altered invocation; SQLite is real.
    executable = tmp_path / 'docker'
    executable.write_text('''#!/usr/bin/env python3
import os, pathlib, subprocess, sys
if pathlib.Path.cwd() != pathlib.Path(os.environ['COMPOSE_DIR']):
    sys.exit(80)
a = sys.argv[1:]
if a == ['compose', 'ps', '--all', '-q', 'api']:
    print('container-id' if os.environ['API_STATE'] != 'missing' else '')
elif a[:2] == ['inspect', '--format'] and a[-1] == 'container-id':
    if os.environ['API_STATE'] == 'inspect-error':
        sys.exit(81)
    print(os.environ['API_STATE'])
elif a[:7] == ['compose', 'exec', '-T', 'api', 'python', '/srv/scripts/database_backup.py', 'backup']:
    sys.exit(subprocess.call([sys.executable, os.environ['HELPER'], 'backup']))
elif a[:7] == ['compose', 'run', '--rm', '--no-deps', 'api', 'python', '/srv/scripts/database_backup.py']:
    sys.exit(subprocess.call([sys.executable, os.environ['HELPER'], *a[7:]]))
else:
    sys.exit(82)
''')
    executable.chmod(0o755)
    def invoke(script, *args, state='false false false'):
        return subprocess.run(['bash', str(SCRIPT.parent / script), *args], cwd=tmp_path,
                              env={**os.environ, 'PATH': f'{tmp_path}:{Path(sys.executable).parent}:/usr/bin:/bin',
                                   'DATABASE_PATH': str(storage[0]), 'BACKUP_DIR': str(storage[1]),
                                   'COMPOSE_DIR': str(SCRIPT.parent.parent), 'HELPER': str(SCRIPT),
                                   'API_STATE': state}, capture_output=True, text=True, timeout=5)
    return invoke


def test_shell_backup_and_stopped_restore_work_from_another_directory(storage, shell):
    name = success(shell('backup.sh'))['backup']
    with sqlite3.connect(storage[0]) as db:
        db.execute("UPDATE evidence SET value='new state'")
    success(shell('restore.sh', name))
    assert values(storage[0]) == [('original',)]


@pytest.mark.parametrize('state', ['true false false', 'true true false', 'false false true',
                                   'missing', 'inspect-error', 'unknown'])
def test_shell_refuses_restore_if_api_is_not_proven_stopped(storage, shell, state):
    name = success(run(storage, 'backup'))['backup']
    with sqlite3.connect(storage[0]) as db:
        db.execute("UPDATE evidence SET value='new state'")
    result = shell('restore.sh', name, state=state)
    assert result.returncode != 0
    assert values(storage[0]) == [('new state',)]


def test_shell_restore_requires_explicit_backup(storage, shell):
    result = shell('restore.sh')
    assert result.returncode != 0
    assert 'usage' in result.stderr.lower()
    assert values(storage[0]) == [('original',)]


def test_configured_seed_requires_marker_before_publishing(storage, monkeypatch):
    monkeypatch.setenv('SEED_CSV_PATH', '/srv/data/account_applications.csv')
    result = run(storage, 'backup')
    assert result.returncode != 0
    assert 'seed' in result.stderr.lower()
    assert not list(storage[1].glob('*.sqlite3'))
