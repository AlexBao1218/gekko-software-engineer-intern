"""Parse and validate a whole CSV before any database write occurs."""
import csv
from dataclasses import dataclass
from datetime import date
import io
import re

from .models import FieldError, RowRejection


FIELDS = ('case_ref', 'applicant_name', 'id_number', 'dob', 'issue_date', 'image_ref')


class InvalidCSV(ValueError):
    def __init__(self, message: str, row: int | None = None):
        super().__init__(message)
        self.row = row


@dataclass
class ParsedImport:
    accepted: list[dict]
    rejected: list[RowRejection]

    @property
    def total_rows(self) -> int:
        return len(self.accepted) + len(self.rejected)


def parse_csv(content: bytes) -> ParsedImport:
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise InvalidCSV('Upload a UTF-8 encoded CSV file.') from exc
    if '\x00' in text:
        raise InvalidCSV('CSV must not contain NUL characters.')
    stream = io.StringIO(text, newline='')
    reader = csv.reader(stream, strict=True)
    parsed = ParsedImport([], [])
    seen: set[str] = set()
    try:
        header = next(reader, [])
        if len(header) != len(FIELDS) or set(header) != set(FIELDS):
            raise InvalidCSV('Header must contain each required column exactly once: ' + ', '.join(FIELDS), 1)
        while True:
            row_number = reader.line_num + 1
            start = stream.tell()
            values = next(reader, None)
            if values is None:
                break
            # Empty physical records are not applications, but still count for line numbers.
            if not values or not text[start:stream.tell()].strip():
                continue
            if len(values) != len(FIELDS):
                raise InvalidCSV('Row must have exactly six columns.', row_number)
            row = dict(zip(header, (value.strip() for value in values)))
            errors = [FieldError(field=field, message='This field is required.')
                      for field, value in row.items() if not value]
            for field in ('dob', 'issue_date'):
                if not row[field]:
                    continue
                try:
                    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', row[field]):
                        raise ValueError()
                    date.fromisoformat(row[field])
                except ValueError:
                    errors.append(FieldError(field=field, message='Use a valid date in YYYY-MM-DD format.'))
            if row['id_number'] and not row['id_number'].startswith('TEST-ID-'):
                errors.append(FieldError(field='id_number', message='Use a synthetic TEST-ID- identifier.'))
            reference = row['case_ref']
            if reference:
                if reference in seen:
                    errors.append(FieldError(field='case_ref', message='Duplicate case_ref in this file.'))
                seen.add(reference)
            if errors:
                parsed.rejected.append(RowRejection(row=row_number, case_ref=reference, errors=errors))
            else:
                parsed.accepted.append({'row_number': row_number, **row})
    except csv.Error as exc:
        raise InvalidCSV('Invalid CSV quoting or a field exceeds the parser size limit.', reader.line_num) from exc
    if not parsed.total_rows:
        raise InvalidCSV('CSV must contain at least one application row.')
    return parsed
