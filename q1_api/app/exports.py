"""Immutable CSV snapshots and a durable delivery queue in the same SQLite file."""
import csv
import hashlib
import io
import json
from uuid import uuid4

from pydantic import ValidationError

from .models import OCRFields
from .ocr_store import now


CSV_COLUMNS = ['case_ref', 'applicant_name', 'id_number', 'dob', 'issue_date', 'image_ref',
               'ocr_full_name', 'ocr_id_number', 'ocr_dob', 'ocr_issue_date', 'ocr_confidence']


class MissingOCRFields(ValueError):
    pass


def initialize_export_schema(connection):
    # Called inside initialize_ocr_schema's transaction, including on Q1 database upgrades.
    connection.execute('''CREATE TABLE IF NOT EXISTS exports (
        id TEXT PRIMARY KEY NOT NULL, batch_id TEXT NOT NULL REFERENCES batches(id),
        created_at TEXT NOT NULL, file_name TEXT NOT NULL UNIQUE,
        idempotency_key TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, content BLOB NOT NULL,
        row_count INTEGER NOT NULL CHECK(row_count > 0),
        status TEXT NOT NULL CHECK(status IN ('pending','delivering','delivered','failed')),
        attempts INTEGER NOT NULL DEFAULT 0, receipt TEXT, error_code TEXT, error TEXT,
        retryable INTEGER NOT NULL DEFAULT 0, delivery_token TEXT)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS export_cases (
        case_id TEXT PRIMARY KEY NOT NULL REFERENCES cases(id),
        export_id TEXT NOT NULL REFERENCES exports(id))''')
    connection.execute('''CREATE TABLE IF NOT EXISTS delivery_attempts (
        id INTEGER PRIMARY KEY, export_id TEXT NOT NULL REFERENCES exports(id),
        token TEXT NOT NULL, attempt INTEGER NOT NULL, outcome TEXT NOT NULL,
        error TEXT, retryable INTEGER NOT NULL DEFAULT 1, receipt TEXT,
        applied INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)''')


class ExportStore:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _detail(row):
        if row is None:
            return None
        result = dict(row)
        result['receipt'] = json.loads(result['receipt']) if result['receipt'] else None
        return result

    def create(self, batch_id):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT 1 FROM batches WHERE id=?', (batch_id,)).fetchone() is None:
                return None
            cases = connection.execute("SELECT * FROM cases WHERE batch_id=? AND status='stage2_approved' "
                                       'ORDER BY case_ref', (batch_id,)).fetchall()
            if not cases:
                return {'count': 0, 'export': None}
            stream = io.StringIO(newline='')
            writer = csv.writer(stream, lineterminator='\n')
            writer.writerow(CSV_COLUMNS)
            for case in cases:
                try:
                    fields = OCRFields.model_validate_json(case['ocr_fields'] or 'null')
                except ValidationError as exc:
                    raise MissingOCRFields('Approved cases must have complete OCR fields before export.') from exc
                writer.writerow([case[column] for column in CSV_COLUMNS[:6]] + [
                    fields.full_name, fields.id_number, fields.dob, fields.issue_date, fields.confidence])
            content = stream.getvalue().encode('utf-8')
            export_id = str(uuid4())
            connection.execute('''INSERT INTO exports
                (id,batch_id,created_at,file_name,idempotency_key,sha256,content,row_count,status)
                VALUES (?,?,?,?,?,?,?,?,'pending')''', (export_id, batch_id, now(),
                f'export-{export_id}.csv', f'export:{export_id}', hashlib.sha256(content).hexdigest(),
                content, len(cases)))
            connection.executemany('INSERT INTO export_cases VALUES (?,?)',
                                   [(case['id'], export_id) for case in cases])
            connection.executemany("UPDATE cases SET status='exported' WHERE id=? AND status='stage2_approved'",
                                   [(case['id'],) for case in cases])
            return {'count': len(cases), 'export': self._detail(connection.execute(
                'SELECT * FROM exports WHERE id=?', (export_id,)).fetchone())}

    def get(self, export_id):
        with self.database.connect() as connection:
            return self._detail(connection.execute('SELECT * FROM exports WHERE id=?', (export_id,)).fetchone())

    def list_batch(self, batch_id):
        with self.database.connect() as connection:
            connection.execute('BEGIN')
            if connection.execute('SELECT 1 FROM batches WHERE id=?', (batch_id,)).fetchone() is None:
                return None
            return [self._detail(row) for row in connection.execute(
                'SELECT * FROM exports WHERE batch_id=? ORDER BY rowid', (batch_id,))]

    def recover(self):
        # One API worker owns the dispatcher. An unfinished delivery is safe to replay
        # because its persisted key and bytes are never replaced.
        with self.database.connect() as connection:
            connection.execute("UPDATE exports SET status='pending',delivery_token=NULL,error_code='interrupted',"
                               "error='Worker stopped; retrying the saved export.',retryable=1 WHERE status='delivering'")

    def retry(self, export_id):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("UPDATE exports SET status='pending',error=NULL,error_code=NULL,retryable=0 "
                               "WHERE id=? AND status='failed'", (export_id,))
            return self._detail(connection.execute('SELECT * FROM exports WHERE id=?', (export_id,)).fetchone())

    def claim(self):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT * FROM exports WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return None
            token = str(uuid4())
            connection.execute("UPDATE exports SET status='delivering',delivery_token=? WHERE id=?", (token, row['id']))
            return {**self._detail(row), 'status': 'delivering', 'delivery_token': token}

    def begin_attempt(self, export):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT attempts FROM exports WHERE id=? AND delivery_token=? "
                                     "AND status='delivering'", (export['id'], export['delivery_token'])).fetchone()
            if row is None:
                return None
            attempt = row['attempts'] + 1
            connection.execute('UPDATE exports SET attempts=? WHERE id=?', (attempt, export['id']))
            return connection.execute('''INSERT INTO delivery_attempts
                (export_id,token,attempt,outcome,created_at) VALUES (?,?,?,'unknown',?)''',
                (export['id'], export['delivery_token'], attempt, now())).lastrowid

    def record(self, export, attempt_id, result, final):
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            valid = connection.execute("SELECT 1 FROM exports WHERE id=? AND delivery_token=? "
                                       "AND status='delivering'", (export['id'], export['delivery_token'])).fetchone() is not None
            receipt = json.dumps(result.receipt) if result.receipt is not None else None
            connection.execute('UPDATE delivery_attempts SET outcome=?,error=?,retryable=?,receipt=?,applied=? '
                               'WHERE id=? AND export_id=? AND token=?',
                               (result.code or 'ok', result.reason, int(result.retryable), receipt, int(valid),
                                attempt_id, export['id'], export['delivery_token']))
            if valid:
                connection.execute('UPDATE exports SET error_code=?,error=?,retryable=? WHERE id=?',
                                   (result.code, result.reason, int(result.retryable), export['id']))
                if final:
                    connection.execute('UPDATE exports SET status=?,receipt=?,delivery_token=NULL WHERE id=?',
                                       ('delivered' if result.receipt is not None else 'failed', receipt, export['id']))
            return valid

    def fail(self, export):
        with self.database.connect() as connection:
            connection.execute("UPDATE exports SET status='failed',error_code='task_interrupted',"
                               "error='Delivery interrupted; retry this saved export.',retryable=1,delivery_token=NULL "
                               "WHERE id=? AND delivery_token=? AND status='delivering'",
                               (export['id'], export['delivery_token']))
