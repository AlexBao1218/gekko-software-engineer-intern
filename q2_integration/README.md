# Module 2: OCR integration and export delivery

Add OCR, export, and delivery modules to `q1_api/app/`. Register the new endpoints on the existing `app.main:app` application. Keep the [fixed repository layout](../README.md#2-fixed-structure-and-naming).

Read the [Module 2 requirements](../README.md#4-module-2-ocr-integration-and-export-delivery-35-points), [OCR contract](../mock_ocr/README.md), and [account-opening contract](../mock_onboarding/README.md) before implementing the clients.

## Run without Docker

Start each service in a separate terminal from the repository root:

```bash
python3 mock_ocr/server.py
```

```bash
python3 mock_onboarding/server.py
```

```bash
cd q1_api
uvicorn app.main:app --reload
```

The API defaults to `http://localhost:9100/ocr/parse` and `http://localhost:9200/ingest`; environment overrides are documented below.

Verify one batch through import, background OCR, both review stages, export, and submission to the account-opening service. Record retry and recovery evidence in [REFLECTION.md](../REFLECTION.md).

## Implemented: Q2.1 and Q2.2 OCR

OCR, recovery and export/delivery extend the original API. The full native
lifecycle is verified; Q3 deployment and backup remain a separate module.

Start the unchanged mock with `python mock_ocr/server.py`, then start **one API
worker** from the repository root:

```bash
OCR_URL=http://127.0.0.1:9100/ocr/parse \
  python -m uvicorn app.main:app --app-dir q1_api --host 127.0.0.1 --port 8000
```

Upload a CSV using Q1, then use its returned batch ID:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/batches/BATCH_ID/process
curl -sS http://127.0.0.1:8000/api/batches/BATCH_ID
curl -sS 'http://127.0.0.1:8000/api/batches/BATCH_ID/cases?status=ocr_failed&page_size=100'
```

`process` returns 202 with batch counts and the current `ocr_run`; it does not
wait for vendor requests. Unknown batches return 404. Concurrent triggers for
the same active batch reuse its run and retry budget. There is one globally
active OCR loop and at most four active/queued batches; a fifth returns 429 with
`Retry-After: 1` without claiming work. An all-rejected/fully processed batch
finishes with zero selected cases and makes no OCR calls.

Case responses retain all original application values and add:

- `ocr_fields`: saved `id_number`, `full_name`, `dob`, `issue_date`, `confidence`.
- `ocr_error_code` / `ocr_error`: current failure classification and safe reason.
- `ocr_retryable`: whether a normal process call can retry this failed case.
- `ocr_attempts`: cumulative observed attempts applied to the case across runs.

Batch `ocr_run` contains `id`, `status`, `error`, `total`, `completed`,
`created_at`, `finished_at`. **A completed run means its attempt budget ended,
not that every case passed OCR.** Inspect status counts and failed cases.
`completed` counts terminal/discarded returned results; a case skipped because
its status/token already changed before a request may not increment this count.
`failed` denotes an unexpected worker fault; `interrupted` denotes worker or
trigger cancellation/restart. Unexpected errors are logged server-side, with
safe staff-visible errors. If storage cannot record the error, the next
successful batch read/process reconciles it; after process loss, startup marks
unfinished runs interrupted instead.

### Configuration and finite retries

| Variable | Default | Meaning |
|---|---|---|
| DATABASE_PATH | repository `data/verification.sqlite3` | Existing Q1 database, additive migration preserves cases/reviews |
| OCR_URL | `http://localhost:9100/ocr/parse` | Full trusted vendor endpoint, HTTP or HTTPS |
| OCR_TIMEOUT_SECONDS | 1 | Both per-phase timeout and whole-attempt deadline |
| OCR_MAX_ATTEMPTS | 6 | Total attempts per selected case per run, including the first; allowed 1–10 |
| OCR_BACKOFF_SECONDS | 0.1 | Initial exponential retry delay, allowed 0–10 |
| OCR_BACKOFF_CAP_SECONDS | 0.5 | Maximum retry delay, allowed 0–10 |

Nonfinite/invalid configuration fails at startup. The defaults are for the local
assessment mock, not a production vendor latency promise. No environment file
or credential needs to be committed.

HTTP 400/422 and invalid successful response schemas are permanent failures.
The mock's readable 422 reason is retained (bounded/sanitized). HTTP 5xx/429,
timeouts and network failures retry with capped exponential backoff. Exhaustion
persists `ocr_failed`, the last reason, attempts and retryability. Responses are
limited to 64 KiB and validated before saving; unknown outcome after timeout is
safe to retry because the OCR contract has no business side effects.

For six attempts, delays are 0.1/0.2/0.4/0.5/0.5 seconds. The maximum network
deadline plus backoff budget is **7.7 seconds per case**, versus 4.7 seconds for
four attempts. A 43-case run has at most 331.1 seconds of those waits. This bound
excludes queue time, scheduler overhead and SQLite calls (each connection has a
one-second lock wait). A persistent dependency failure ends as visible failure;
there is no automatic infinite retry loop.

### Recovery and approval safety

Call the same `process` endpoint after a crash or transient exhaustion. It only
selects `pending` and retryable `ocr_failed`; successful, approved, rejected,
exported and permanently unreadable cases are excluded. Each rerun gets another
finite budget. For bad scans or request/schema errors, inspect the reason and
correct the source/configuration rather than blindly retrying. Corrected source
data can be uploaded as a new batch; this exercise has no image-editing UI.

Cases keep `pending` (or their previous retryable failure) while a request is in
flight. There is no stranded case-level `processing` status. Each run claims a
UUID token; an applied result must still match both that token and its original
eligible status inside a short transaction. State/result/attempt history commit
together. A delayed old success, old failure or cancelled database thread cannot
overwrite a replacement run or human approval. No HTTP/backoff waits hold a DB
transaction. Completed attempts and run history remain in SQLite after recovery;
in-flight attempts killed before persistence may only appear in mock debug logs.

Tasks and the HTTP client belong to app lifespan. Shutdown cancels and awaits
tasks before closing the client. Handler cancellation during a DB claim waits
for that claim then marks it interrupted, so it cannot create a running orphan.
**Use one Uvicorn worker.** Tokens prevent stale writes, but this implementation
is not a distributed scheduler; multiple workers/instances can disrupt each
other's task ownership and startup recovery.

### Verification

```bash
python -m pytest q1_api/
python q1_api/tests/ocr_experiment.py --output /tmp/gekko-ocr-evidence-new
```

The manual experiment needs a new output directory. It uses fresh unchanged mock
processes with seeds 42/7/99, default failure probabilities and 600-second hangs
(cut off by client deadlines). It records both four/six-attempt runs, raw request
outcomes, and clean versus SIGKILL-interrupted full-field/status comparisons,
with at most three recovery rounds. Its overall `pass` concerns experiment
execution/recovery equivalence; an individual measurement may correctly report
single-round exhaustion. Pytest separately uses short controlled faults.

See [OCR acceptance and measured results](evidence/OCR-ACCEPTANCE.zh-CN.md) and
[implementation decisions](../Q2_OCR_IMPLEMENTATION.zh-CN.md).

## Implemented: Q2.3 exports and account-opening delivery

Start the unchanged `mock_onboarding/server.py` beside the OCR service and API.
After approving a case at stages 1 and 2, create an export:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/batches/BATCH_ID/export
curl -sS http://127.0.0.1:8000/api/batches/BATCH_ID/exports
curl -sS http://127.0.0.1:8000/api/exports/EXPORT_ID
curl -fS http://127.0.0.1:8000/api/exports/EXPORT_ID/download -o approved.csv
curl -sS -X POST http://127.0.0.1:8000/api/exports/EXPORT_ID/deliver
curl -sS http://127.0.0.1:9200/debug/deliveries
```

Creating a new export returns **202** with `{count, export}`. Only currently
`stage2_approved` cases are included. Snapshot, unique case membership and status
change to `exported` commit together. An empty selection returns **200** with
`{"count":0,"export":null}` and creates nothing. Unknown IDs return 404; approved
cases missing complete OCR data return 409 without consuming any approvals.
Concurrent export calls cannot select the same cases. Later approvals produce a
new export with a new key. Cases are never put back into approval on delivery failure.

Export detail exposes `id`, `batch_id`, `created_at`, `file_name`, `idempotency_key`,
`sha256`, `row_count`, `status`, `attempts`, `receipt`, `error_code`, `error`, and
`retryable`. Poll status to distinguish `pending`, `delivering`, `delivered`, and
`failed`; HTTP 202 is acceptance of work, not proof of delivery. The batch exports
list recovers IDs if the original export response was lost. Download returns the
saved CSV bytes, not a regeneration from potentially changed source data.

CSV is UTF-8 with LF line endings and standard CSV quoting, sorted by `case_ref`:

```text
case_ref,applicant_name,id_number,dob,issue_date,image_ref,ocr_full_name,ocr_id_number,ocr_dob,ocr_issue_date,ocr_confidence
```

The original six input columns remain separate from the saved OCR values.
SQLite stores the immutable CSV BLOB, UUID filename, SHA-256 and `export:<UUID>`
key. Each send uses these exact bytes and the same `Idempotency-Key` and
`X-File-Name` headers. Both the saved content hash and returned receipt identity,
filename, hash and types are checked. A mismatched receipt never marks delivery
successful. CSV and membership are persisted before any external HTTP call.

### Delivery configuration and recovery

| Variable | Default | Meaning |
|---|---|---|
| DELIVERY_URL | `http://localhost:9200/ingest` | Full trusted downstream HTTP(S) endpoint |
| DELIVERY_TIMEOUT_SECONDS | 1 | Per-phase timeout and whole-attempt deadline |
| DELIVERY_MAX_ATTEMPTS | 4 | Total attempts per round, allowed 1–10 |
| DELIVERY_BACKOFF_SECONDS | 0.1 | Initial exponential delay, allowed 0–10 |
| DELIVERY_BACKOFF_CAP_SECONDS | 0.5 | Maximum delay, allowed 0–10 |

One dispatcher serially drains durable pending exports. Network attempts have a
finite budget: defaults give at most 4.7s of deadlines/backoff per round, excluding
queue/storage/scheduling overhead. The controlled lost-response drill succeeds
on the second attempt; four attempts also allow bounded transient pre-ingestion
failures. This is a local exercise choice, not a measured real-vendor SLA.
HTTP 5xx/429, timeout and network errors can retry; HTTP 400, invalid receipts and
receipt mismatches end the round. Responses have a 64 KiB size limit. Failed
exports remain queryable and require an explicit `deliver` call for a new finite
round, including after correcting a permanent error. Repeated calls while pending,
delivering or already delivered do not create another delivery job.

A timeout leaves ingestion **unconfirmed**, not known to have failed. Recovery
reuses the same bytes/key, so the original mock returns its original receipt.
After SIGKILL, startup moves unfinished `delivering` records to `pending` and
replays automatically. Graceful shutdown or an unexpected task failure marks an
in-flight export `failed` and retryable; call `deliver` to resume. Pending work is
persisted even if the creating HTTP request is cancelled. Already delivered
exports are never replayed automatically. Storage failure delays error recording
until storage returns, before the dispatcher moves to new network work.

`attempts` counts durable attempt intents recorded **before** network I/O; a
crash can leave an `unknown` attempt that was not actually sent. The internal
`delivery_attempts` table preserves outcomes and whether each write applied.
Conditional token/status writes prevent old SQLite threads from overwriting a
newer receipt. No network wait holds a write transaction.

Use **one Uvicorn worker**, as for OCR. The mock's deduplication data is in memory;
do not restart/reset it during recovery verification. Actual downstream services
must offer the same idempotency scope/retention for this guarantee to hold.

```bash
python -m pytest q1_api/
python q1_api/tests/delivery_experiment.py --output /tmp/gekko-delivery-evidence-new
```

Use a new evidence directory. The experiment starts the original services on
local ephemeral ports, verifies the complete sample lifecycle, deliberately
loses a response after ingestion, and SIGKILLs the API before receipt persistence.
It retains CSV bytes, request traces, local receipts, downstream debug records
and the crash boundary. See [Q2 acceptance](evidence/Q2-ACCEPTANCE-20261013.md)
and [delivery design/process](../Q2_DELIVERY_IMPLEMENTATION.zh-CN.md).
