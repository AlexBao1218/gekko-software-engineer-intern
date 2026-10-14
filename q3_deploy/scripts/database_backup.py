#!/usr/bin/env python3
"""Online SQLite snapshots and offline restoration. Invoke restore through restore.sh."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
from uuid import uuid4


NAME = re.compile(r'backup-\d{8}T\d{12}Z-[0-9a-f]{8}\.sqlite3')
# Columns consumed by the current application; extra tables (including seed_imports)
# remain part of the full snapshot. An arbitrary valid SQLite file is not a backup.
COLUMNS = {
    'batches': 'id created_at total_rows accepted_count rejected_count rejected_rows',
    'cases': 'id batch_id row_number case_ref applicant_name id_number dob issue_date image_ref status '
             'ocr_fields ocr_error ocr_error_code ocr_retryable ocr_attempts ocr_token',
    'reviews': 'id case_id stage decision previous_status new_status reviewed_at',
    'ocr_runs': 'id batch_id status error total completed created_at finished_at',
    'ocr_attempts': 'id run_id case_id attempt outcome error retryable fields applied created_at',
    'exports': 'id batch_id created_at file_name idempotency_key sha256 content row_count status '
               'attempts receipt error_code error retryable delivery_token',
    'export_cases': 'case_id export_id',
    'delivery_attempts': 'id export_id token attempt outcome error retryable receipt applied created_at',
}


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError('Database operation exceeded BACKUP_TIMEOUT_SECONDS')


def validate(connection, deadline):
    check_deadline(deadline)
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('SQLite integrity check failed')
    if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('SQLite foreign key check failed')
    for table, columns in COLUMNS.items():
        connection.execute(f'SELECT {",".join(columns.split())} FROM {table} LIMIT 0')
    if (os.environ.get('SEED_CSV_PATH') or
            connection.execute("SELECT 1 FROM sqlite_master WHERE name='seed_imports'").fetchone()):
        connection.execute('SELECT seed_key, sha256, batch_id FROM seed_imports LIMIT 0')
    check_deadline(deadline)


def snapshot(source, destination, deadline):
    """Copy committed pages (including WAL) into a validated, self-contained staging file."""
    if source.is_symlink() or not source.is_file():
        raise ValueError('Database source must be an existing regular file')
    check_deadline(deadline)
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=.05)) as reader:
        with closing(sqlite3.connect(destination, timeout=.05)) as writer:
            reader.backup(writer, pages=128, sleep=.01,
                          progress=lambda *_: check_deadline(deadline))
            writer.execute('PRAGMA journal_mode=DELETE')
            validate(writer, deadline)
    with destination.open('rb') as saved:
        os.fsync(saved.fileno())


@contextmanager
def staging(directory):
    fd, name = tempfile.mkstemp(prefix='.database-', suffix='.tmp', dir=directory)
    os.close(fd)
    path = Path(name)
    try:
        yield path
    finally:
        for suffix in ('', '-wal', '-shm', '-journal'):
            Path(str(path) + suffix).unlink(missing_ok=True)


@contextmanager
def operation_lock(directory):
    fd = os.open(directory / '.database.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another backup or restore operation is active') from None
        yield
    finally:
        os.close(fd)  # Never unlink: all contenders must lock the same inode.


def backup_name(after=None):
    timestamp = datetime.now(timezone.utc)
    if after is not None:
        previous = datetime.strptime(after.split('-')[1], '%Y%m%dT%H%M%S%fZ').replace(tzinfo=timezone.utc)
        if timestamp <= previous:
            try:
                timestamp = previous + timedelta(microseconds=1)
            except OverflowError:
                raise ValueError('Backup timestamp exhausted; inspect existing backup names') from None
    return f'backup-{timestamp:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}.sqlite3'


def listing(directory):
    return sorted(p.name for p in directory.iterdir()
                  if NAME.fullmatch(p.name) and not p.is_symlink() and p.is_file())


def backup(source, directory, deadline):
    before = listing(directory)
    # The operation lock serializes this watermark with publication/rotation.
    # Advance from disk, not process memory: restarts and clock rollback are safe.
    name = backup_name(after=before[-1] if before else None)
    with staging(directory) as temporary:
        snapshot(source, temporary, deadline)
        os.replace(temporary, directory / name)
    # Publish first; an invalid/incomplete snapshot must never trigger retention.
    for old in listing(directory)[:-7]:
        (directory / old).unlink()
    return {'operation': 'backup', 'backup': name, 'before': before, 'after': listing(directory)}


def restore(target, directory, name, deadline):
    if not NAME.fullmatch(name):
        raise ValueError('Restore requires a backup basename from the backup listing')
    source = directory / name
    if target.is_symlink():
        raise ValueError('Restore target must not be a symlink')
    target.parent.mkdir(parents=True, exist_ok=True)
    safety = None
    with staging(target.parent) as temporary:
        snapshot(source, temporary, deadline)
        if target.exists():
            safety_directory = directory / 'pre-restore'
            safety_directory.mkdir(exist_ok=True)
            safety = 'pre-restore/' + backup_name()
            try:
                with staging(safety_directory) as previous:
                    snapshot(target, previous, deadline)
                    os.replace(previous, directory / safety)
                # Offline only. Fold existing WAL into its old database before removing
                # sidecars, so a later failed replace still leaves a usable old database.
                with closing(sqlite3.connect(target.resolve().as_uri() + '?mode=rw', uri=True, timeout=.05)) as db:
                    if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
                        raise RuntimeError('Existing database is busy')
            except (sqlite3.Error, OSError, ValueError, RuntimeError) as error:
                raise RuntimeError('Cannot preserve current database; keep API stopped and quarantine '
                                   'the database together with its -wal/-shm/-journal files before retrying') from error
        check_deadline(deadline)
        for suffix in ('-wal', '-shm', '-journal'):
            Path(str(target) + suffix).unlink(missing_ok=True)
        os.replace(temporary, target)
    return {'operation': 'restore', 'restored': name, 'safety_backup': safety}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'restore'])
    parser.add_argument('name', nargs='?')
    args = parser.parse_args()
    try:
        if (args.operation == 'restore') != (args.name is not None):
            raise ValueError('Usage: database_backup.py backup | restore BACKUP_BASENAME')
        seconds = float(os.environ.get('BACKUP_TIMEOUT_SECONDS', '30'))
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('BACKUP_TIMEOUT_SECONDS must be finite and positive')
        deadline = time.monotonic() + seconds
        source = Path(os.environ.get('DATABASE_PATH', '/data/app.db'))
        directory = Path(os.environ.get('BACKUP_DIR', '/backups'))
        directory.mkdir(parents=True, exist_ok=True)
        with operation_lock(directory):
            result = (backup(source, directory, deadline) if args.operation == 'backup'
                      else restore(source, directory, args.name, deadline))
        print(json.dumps(result))
        return 0
    except (sqlite3.Error, OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
