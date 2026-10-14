"""Probe the existing SQLite schema and actual write/journal access, then roll back."""
import sqlite3
from pathlib import Path


def database_ready(path: Path) -> bool:
    connection = None
    try:
        # mode=rw refuses to create a missing database; URI quoting preserves ?/# in paths.
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=.2)
        connection.execute('BEGIN IMMEDIATE')
        for query in (
            'SELECT id, accepted_count, rejected_count FROM batches LIMIT 1',
            'SELECT id, status, ocr_fields FROM cases LIMIT 1',
            'SELECT case_id, new_status FROM reviews LIMIT 1',
            'SELECT id, status FROM ocr_runs LIMIT 1',
            'SELECT run_id, case_id, applied FROM ocr_attempts LIMIT 1',
            'SELECT id, length(content), receipt FROM exports LIMIT 1',
            'SELECT case_id, export_id FROM export_cases LIMIT 1',
            'SELECT export_id, outcome, applied FROM delivery_attempts LIMIT 1',
        ):
            connection.execute(query).fetchone()
        # A lock alone does not prove journal creation works (e.g. read-only /data).
        # This real main-database write rolls back, leaving no probe table or business change.
        connection.execute('CREATE TABLE __readiness_probe (value INTEGER)')
        connection.execute('INSERT INTO __readiness_probe VALUES (1)')
        connection.rollback()
        return True
    except (sqlite3.DatabaseError, OSError, ValueError):
        return False
    finally:
        if connection is not None:
            connection.close()
