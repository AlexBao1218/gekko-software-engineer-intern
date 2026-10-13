"""Durable OCR progress and fenced writes; no network calls inside transactions."""
from datetime import datetime, timezone
import json
from uuid import uuid4


ELIGIBLE = "(status = 'pending' OR (status = 'ocr_failed' AND ocr_retryable = 1))"


def now():
    return datetime.now(timezone.utc).isoformat()


def initialize_ocr_schema(connection):
    connection.execute('BEGIN IMMEDIATE')
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(cases)')}
    for name, declaration in {
        'ocr_fields': 'TEXT', 'ocr_error': 'TEXT', 'ocr_error_code': 'TEXT',
        'ocr_retryable': 'INTEGER NOT NULL DEFAULT 0', 'ocr_attempts': 'INTEGER NOT NULL DEFAULT 0',
        'ocr_token': 'TEXT',
    }.items():
        if name not in columns:
            connection.execute(f'ALTER TABLE cases ADD COLUMN {name} {declaration}')
    connection.execute('''CREATE TABLE IF NOT EXISTS ocr_runs (
        id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
        status TEXT NOT NULL, error TEXT, total INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, finished_at TEXT)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS ocr_attempts (
        id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES ocr_runs(id),
        case_id TEXT NOT NULL REFERENCES cases(id), attempt INTEGER NOT NULL,
        outcome TEXT NOT NULL, error TEXT, retryable INTEGER NOT NULL,
        fields TEXT, applied INTEGER NOT NULL, created_at TEXT NOT NULL)''')


class OCRStore:
    def __init__(self, database):
        self.database = database

    def interrupt_runs(self):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("UPDATE ocr_runs SET status='interrupted', error=?, finished_at=? "
                               "WHERE status='running'", ('Worker stopped; call process to resume.', now()))
            connection.execute('UPDATE cases SET ocr_token=NULL WHERE ocr_token IS NOT NULL')

    def start(self, batch_id):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT 1 FROM batches WHERE id=?', (batch_id,)).fetchone() is None:
                return None
            candidates = [dict(row) for row in connection.execute(
                f'SELECT * FROM cases WHERE batch_id=? AND {ELIGIBLE} ORDER BY row_number', (batch_id,))]
            run_id = str(uuid4())
            connection.execute('INSERT INTO ocr_runs (id,batch_id,status,total,created_at,finished_at) '
                               'VALUES (?,?,?,?,?,?)', (run_id, batch_id,
                               'running' if candidates else 'completed', len(candidates), now(),
                               None if candidates else now()))
            connection.execute(f'UPDATE cases SET ocr_token=? WHERE batch_id=? AND {ELIGIBLE}',
                               (run_id, batch_id))
            return run_id, candidates

    def eligible(self, case_id, run_id, expected_status):
        with self.database.connect() as connection:
            return connection.execute(f'SELECT 1 FROM cases WHERE id=? AND ocr_token=? AND status=? '
                                      f'AND {ELIGIBLE}', (case_id, run_id, expected_status)).fetchone() is not None

    def record(self, run_id, case, attempt, result, final):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            valid = connection.execute(f'SELECT 1 FROM cases WHERE id=? AND ocr_token=? AND status=? '
                                       f'AND {ELIGIBLE}', (case['id'], run_id, case['status'])).fetchone() is not None
            fields = json.dumps(result.fields) if result.fields is not None else None
            connection.execute('''INSERT INTO ocr_attempts
                (run_id,case_id,attempt,outcome,error,retryable,fields,applied,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)''', (run_id, case['id'], attempt, result.code or 'ok',
                result.reason, int(result.retryable), fields, int(valid), now()))
            if valid:
                connection.execute('UPDATE cases SET ocr_attempts=ocr_attempts+1 WHERE id=?', (case['id'],))
                if final:
                    connection.execute('''UPDATE cases SET status=?,ocr_fields=?,ocr_error=?,
                        ocr_error_code=?,ocr_retryable=?,ocr_token=NULL WHERE id=?''', (
                        'ocr_done' if result.fields is not None else 'ocr_failed', fields, result.reason,
                        result.code, int(result.retryable), case['id']))
            if final or not valid:
                connection.execute('UPDATE ocr_runs SET completed=completed+1 WHERE id=?', (run_id,))
            return valid

    def finish(self, run_id, error=None, interrupted=False):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if error and not interrupted:
                connection.execute(f"UPDATE cases SET status='ocr_failed',ocr_error=?,ocr_error_code='task_error',"
                                   f'ocr_retryable=1 WHERE ocr_token=? AND {ELIGIBLE}', (error, run_id))
            connection.execute('UPDATE cases SET ocr_token=NULL WHERE ocr_token=?', (run_id,))
            connection.execute("UPDATE ocr_runs SET status=?,error=?,finished_at=? WHERE id=? AND status='running'",
                               ('interrupted' if interrupted else 'failed' if error else 'completed',
                                error, now(), run_id))
