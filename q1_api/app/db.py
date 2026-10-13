"""SQLite persistence: each operation owns its connection and transaction."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .imports import ParsedImport
from .models import CaseStatus
from .workflow import InvalidTransition, next_review_status


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=1.0)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Only enum literals enter DDL; request data always uses bound parameters.
        states = ', '.join(f"'{status.value}'" for status in CaseStatus)
        with self.connect() as connection:
            connection.executescript(f'''
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY NOT NULL,
                    created_at TEXT NOT NULL,
                    total_rows INTEGER NOT NULL CHECK (total_rows > 0),
                    accepted_count INTEGER NOT NULL CHECK (accepted_count >= 0),
                    rejected_count INTEGER NOT NULL CHECK (rejected_count >= 0),
                    rejected_rows TEXT NOT NULL,
                    CHECK (total_rows = accepted_count + rejected_count)
                );
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY NOT NULL,
                    batch_id TEXT NOT NULL REFERENCES batches(id),
                    row_number INTEGER NOT NULL CHECK (row_number >= 2),
                    case_ref TEXT NOT NULL CHECK (length(trim(case_ref)) > 0),
                    applicant_name TEXT NOT NULL CHECK (length(trim(applicant_name)) > 0),
                    id_number TEXT NOT NULL CHECK (length(trim(id_number)) > 0),
                    dob TEXT NOT NULL CHECK (length(trim(dob)) > 0),
                    issue_date TEXT NOT NULL CHECK (length(trim(issue_date)) > 0),
                    image_ref TEXT NOT NULL CHECK (length(trim(image_ref)) > 0),
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ({states})),
                    UNIQUE (batch_id, case_ref),
                    UNIQUE (batch_id, row_number)
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    stage INTEGER NOT NULL CHECK (stage IN (1, 2)),
                    decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
                    previous_status TEXT NOT NULL,
                    new_status TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL
                );
            ''')

    def import_batch(self, parsed: ParsedImport) -> dict:
        batch_id = str(uuid4())
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('INSERT INTO batches VALUES (?, ?, ?, ?, ?, ?)', (
                batch_id, datetime.now(timezone.utc).isoformat(), parsed.total_rows,
                len(parsed.accepted), len(parsed.rejected),
                json.dumps([row.model_dump() for row in parsed.rejected], ensure_ascii=False)))
            connection.executemany('''
                INSERT INTO cases (id, batch_id, row_number, case_ref, applicant_name,
                                   id_number, dob, issue_date, image_ref)
                VALUES (:id, :batch_id, :row_number, :case_ref, :applicant_name,
                        :id_number, :dob, :issue_date, :image_ref)
            ''', ({'id': str(uuid4()), 'batch_id': batch_id, **row} for row in parsed.accepted))
            return self._batch_details(connection, batch_id)

    @staticmethod
    def _batch_details(connection, batch_id: str) -> dict | None:
        row = connection.execute('SELECT * FROM batches WHERE id = ?', (batch_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['rejected_rows'] = json.loads(result['rejected_rows'])
        counts = {status.value: 0 for status in CaseStatus}
        counts.update({row['status']: row['count'] for row in connection.execute(
            'SELECT status, COUNT(*) AS count FROM cases WHERE batch_id = ? GROUP BY status', (batch_id,))})
        result['status_counts'] = counts
        return result

    def get_batch(self, batch_id: str) -> dict | None:
        with self.connect() as connection:
            connection.execute('BEGIN')
            return self._batch_details(connection, batch_id)

    def list_cases(self, batch_id: str, status: CaseStatus | None,
                   page: int, page_size: int) -> dict | None:
        with self.connect() as connection:
            connection.execute('BEGIN')
            if connection.execute('SELECT 1 FROM batches WHERE id = ?', (batch_id,)).fetchone() is None:
                return None
            where, parameters = 'batch_id = ?', [batch_id]
            if status is not None:
                where += ' AND status = ?'
                parameters.append(status.value)
            total = connection.execute(f'SELECT COUNT(*) FROM cases WHERE {where}', parameters).fetchone()[0]
            offset = (page - 1) * page_size
            # Very large user page numbers must not overflow SQLite's signed integer binding.
            items = [] if offset >= total else [dict(row) for row in connection.execute(
                f'SELECT * FROM cases WHERE {where} ORDER BY row_number LIMIT ? OFFSET ?',
                [*parameters, page_size, offset])]
            return {'items': items, 'total': total, 'page': page, 'page_size': page_size}

    def review_case(self, case_id: str, stage: int, decision: str) -> dict | None:
        with self.connect() as connection:
            # Serialize writers before reading the status; losers see the winner's commit.
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM cases WHERE id = ?', (case_id,)).fetchone()
            if row is None:
                return None
            current = CaseStatus(row['status'])
            target = next_review_status(current, stage, decision)
            updated = connection.execute('UPDATE cases SET status = ? WHERE id = ? AND status = ?',
                                         (target.value, case_id, current.value))
            if updated.rowcount != 1:
                raise InvalidTransition(current, stage, decision)
            record = {'stage': stage, 'decision': decision, 'previous_status': current.value,
                      'new_status': target.value, 'reviewed_at': datetime.now(timezone.utc).isoformat()}
            connection.execute('''INSERT INTO reviews
                (case_id, stage, decision, previous_status, new_status, reviewed_at)
                VALUES (:case_id, :stage, :decision, :previous_status, :new_status, :reviewed_at)
            ''', {'case_id': case_id, **record})
            return {**dict(row), 'status': target.value, 'review': record}
