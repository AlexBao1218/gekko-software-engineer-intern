# Module 1: Core API

Implement the API in `app/` and tests in `tests/`. Keep the entry point importable as `app.main:app`. Organize database access, validation, and transition rules into modules within `app/`.

The [Module 1 requirements](../README.md#3-module-1-core-api-35-points) define endpoints, review behavior, and test coverage. The [repository layout](../README.md#2-fixed-structure-and-naming) defines fixed paths and names.

## Start the API

From the repository root:

```bash
python -m pip install -r requirements.txt
cd q1_api
uvicorn app.main:app --reload
```

The API reads `DATABASE_PATH` at startup. Its default is
`data/verification.sqlite3` under the repository root, independent of the working
directory. Parent directories and tables are created at startup. Importing the
Python module alone does not create a database. Set `DATABASE_PATH` before
starting Uvicorn to use a separate database. No Docker is required for Q1.

## Implemented: batch import and queries (T1)

From the repository root, upload a multipart field named `file`:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/batches \
  -F 'file=@data/account_applications.csv;type=text/csv'
```

The response is HTTP 201 with `id`, `created_at`, `total_rows`, `accepted_count`,
`rejected_count`, `rejected_rows`, and `status_counts`. The supplied sample has
50 application rows: 43 accepted and 7 rejected. Each rejection contains the
starting physical CSV `row` (header is line 1), `case_ref`, and an `errors` list
of `{field, message}` objects. Every accepted case starts as `pending`.

Use the returned batch ID:

```bash
curl -sS 'http://127.0.0.1:8000/api/batches/BATCH_ID'
curl -sS 'http://127.0.0.1:8000/api/batches/BATCH_ID/cases?status=pending&page=1&page_size=20'
```

Replace `BATCH_ID` with the actual ID. The case list returns `items`, filtered
`total`, `page`, and `page_size`. It orders by original row number. Page starts
at 1; page_size is 1–100. An out-of-range page is empty. Unknown batches return
404; invalid status/pagination parameters return 422. Batch status counts include
all eight supported states, including zero counts.

### Import policy

- UTF-8 (optional BOM); the six specified headers must occur exactly once.
  Column order may change; extra, missing or duplicate headers are rejected.
- Strip leading/trailing whitespace from values. All six fields are required.
  Dates must be real dates in exact `YYYY-MM-DD` format; identity numbers must
  start with the synthetic `TEST-ID-` prefix. No extra age/date-order rules.
- Duplicate references are scoped to one file/batch. The first nonempty
  reference reserves its place even if that row fails validation. Later
  duplicates are rejected. Uploading the same file again creates another batch.
- Empty physical lines are ignored. Multi-line quoted values are supported;
  reported row numbers refer to the beginning of the record. A six-column row
  with blank values is a rejected application, not an ignored line.
- Bad application values reject that row only. A file with all rejected rows
  still creates a batch. Empty/header-only files, invalid encoding, NUL characters, damaged
  CSV quoting or incorrect column counts return 400 and create no batch. A quoted
  empty one-column record is malformed, not a blank physical line.
- CSV file limit: 2 MiB (413 above this). Python's CSV parser also bounds a field
  to 131,072 characters by default; exceeding that limit returns 400. The file
  size check occurs after multipart parsing; Q3 will also cap the proxy request.
- Parse the entire file before starting a short SQLite write transaction.
  Batch, cases and rejection records commit together or roll back together.
  Foreign keys, required fields and uniqueness are also enforced in SQLite.
- SQLite waits up to one second for a lock. Storage unavailability returns
  503 with `Retry-After: 1`; internal integrity errors return a sanitized 500.
  Neither returns SQL, local paths or a traceback to the client.

## Implemented: two-stage review (T2)

`POST /api/cases/{id}/review` accepts JSON with exactly `stage` (integer 1 or 2)
and `decision` (`approve` or `reject`). Replace `CASE_ID` with an ID from a case list:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/cases/CASE_ID/review \
  -H 'Content-Type: application/json' -d '{"stage":1,"decision":"approve"}'
```

| Current status | Stage | Decision | New status |
|---|---:|---|---|
| ocr_done | 1 | approve | stage1_approved |
| ocr_done | 1 | reject | stage1_rejected |
| stage1_approved | 2 | approve | stage2_approved |
| stage1_approved | 2 | reject | stage2_rejected |

Every other combination returns 409 with `code=invalid_transition`, a reason,
current_status, stage and decision. This includes pending/failed OCR, skipping
stage 1, repeating/reversing an earlier decision, and either rejected state.
Unknown cases return 404. Missing/unknown properties, invalid decisions,
non-integer stages (including booleans, strings and floats) return 422.
Duplicate JSON field names also return 422 (`code=duplicate_json_field`), even
when their values match or the field name is written with a Unicode escape.
The API never silently chooses one of two review decisions in the same body.

HTTP 200 returns the updated case and a `review` object containing stage,
decision, previous_status, new_status and UTC reviewed_at. The database keeps
append-only `reviews` rows. The state change and audit insert commit together;
an insert failure rolls both back. `BEGIN IMMEDIATE` serializes SQLite writers
before reading the status, and the UPDATE also checks the old status. With two
concurrent requests at the same stage, one succeeds and the other reads the
new state and returns 409. Prolonged external lock contention can still return
the documented retryable 503.

Transition rules live only in `app/workflow.py`. No review history is deleted
or made unique per stage, so a future return-to-review feature can keep earlier
decisions. That feature is not currently allowed. The database CHECK enumerates
valid states; adding a new named state also requires a schema migration.

`GET /api/health` remains available. OCR processing/recovery is now implemented;
see [Q2 OCR and delivery usage](../q2_integration/README.md). Export/delivery are
also implemented. Q3 now adds `/api/ready` and optional atomic startup seeding;
see [deployment usage](../q3_deploy/README.md). Backup/restore remain pending. All freshly imported cases are pending;
isolated Q1 review tests use explicit database fixtures to establish OCR completion.
There is no production endpoint that bypasses OCR. Approval does not claim
the actor is authenticated: authentication/roles were not part of core Q1.

## Run tests

From the repository root:

```bash
python -m pytest q1_api/
```

Tests use temporary SQLite databases. They cover sample rejection details,
validation/CSV boundaries, persistence, filtering/pagination, transactional
rollback via an injected database trigger, lock contention and concurrent imports.
`tests/test_native_process.py` starts a real localhost API on an ephemeral port,
uploads the sample, restarts the process and verifies persisted results. It uses
POSIX file-descriptor passing (tested on macOS; intended for Linux CI).

The current installed Starlette TestClient emits a deprecation warning for its
httpx integration. It does not fail the tests; it is not suppressed and no unused
httpx2 dependency was added. Final dependency pinning/compatibility remains part
of delivery validation.

Review tests cover all 32 state/stage/decision combinations, two-stage success,
terminal rejections, duplicate/reversed decisions, strict payload validation,
simultaneous same/opposite decisions at both stages, audit-insert rollback,
and persistence. Native HTTP tests additionally cover reviews and their records
across actual API process restart. Q2 native tests now cover real OCR through
both review stages, OCR crash recovery, exact exports and idempotent delivery
through real response loss and API SIGKILL/restart.

Additional edge-case verification covers eight-way conflicts, simultaneous
different stages, two actual API processes sharing storage, commit-blocking
reader locks, update failure and killed writers before/after commit. See the
[full case list and limits](evidence/Q1-REVIEW-EDGE-CASES.zh-CN.md).
Two different stages can both succeed when stage 1 approval commits first.
If a response is lost after commit, re-query the case; retry returns 409 rather
than replaying the original response. Authentication, actor separation,
review reasons and tamper-resistant audit storage are not implemented.
