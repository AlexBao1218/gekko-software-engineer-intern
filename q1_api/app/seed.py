"""Atomically import the configured startup sample once per database."""
import hashlib
from pathlib import Path

from .db import Database
from .imports import InvalidCSV, parse_csv


SEED_KEY = 'account_applications_v1'


def bootstrap_seed(database: Database, path: Path) -> str:
    """Return the seed batch ID; refuse source changes rather than replacing data."""
    try:
        content = path.read_bytes()
    except OSError:
        raise ValueError('Seed CSV could not be read.') from None
    try:
        parsed = parse_csv(content)
    except InvalidCSV:
        raise ValueError('Seed CSV is invalid; check its encoding, columns and rows.') from None
    digest = hashlib.sha256(content).hexdigest()
    with database.connect() as connection:
        # Serialize the marker check and import across concurrent startup processes.
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('''CREATE TABLE IF NOT EXISTS seed_imports (
            seed_key TEXT PRIMARY KEY NOT NULL,
            batch_id TEXT NOT NULL REFERENCES batches(id),
            sha256 TEXT NOT NULL)''')
        existing = connection.execute('SELECT batch_id,sha256 FROM seed_imports WHERE seed_key=?',
                                      (SEED_KEY,)).fetchone()
        if existing is not None:
            if existing['sha256'] != digest:
                raise ValueError('Seed CSV has changed; restore the original configured source.')
            return existing['batch_id']
        batch = database.insert_batch(connection, parsed)
        connection.execute('INSERT INTO seed_imports (seed_key,batch_id,sha256) VALUES (?,?,?)',
                           (SEED_KEY, batch['id'], digest))
        return batch['id']
