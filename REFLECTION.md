# Implementation reflection

Complete each section before submission. Replace bracketed prompts with decisions, measurements, or evidence. Use `Not completed` or `Not measured` where applicable, and explain the gap. Reviewers assess this record alongside the code.

## 1. Design decisions

| Decision | Rationale |
|---|---|
| Database | SQLite via Python's standard library. At T1, batches and cases are stored in a single file; import metadata, rejected-row details and accepted cases commit in one short transaction. This keeps native startup simple and provides atomic import behavior at the exercise's scale. Foreign keys, required values and per-batch reference uniqueness are enforced in storage. One-second lock waits bound contention; temporary unavailability is reported as 503. |
| Background OCR processing | Lifespan-owned asyncio tasks with one globally serial OCR loop and at most four active/queued batches, explicitly one Uvicorn worker. Trigger returns 202; repeated active triggers reuse the run. SQLite stores runs and observed attempts; cases keep pending/retryable failure while waiting. UUID run tokens plus eligible-state guards fence late writes and preserve human approval. No network call holds a DB transaction. |
| Review state machine | `q1_api/app/workflow.py` holds four explicit transitions keyed by current status, stage and decision; every other transition is rejected with 409. `stage2_rejected` makes rejection at either stage terminal. SQLite `BEGIN IMMEDIATE` plus an old-status conditional UPDATE prevent competing decisions from both succeeding. The case change and append-only review record are one transaction; record insertion failure rolls back the status change. |
| Delivery idempotency key | One persisted UUID export uses `export:<UUID>` on every send. CSV bytes, filename, SHA-256, unique membership and case status are saved in one transaction before HTTP. A separate delivery state distinguishes snapshot creation from confirmed ingestion. Receipt key/file/hash must match. Durable serial dispatch, token-guarded writes and same-key replay recover unknown results. |

## 2. Mock observations and retry configuration

| Item | Measurement or decision |
|---|---|
| Observed OCR failure rates | Measured against original mock with seeds 42/7/99, each using fresh four/six-attempt runs; raw attempts and actual rates are in q2_integration/evidence/OCR-ACCEPTANCE.zh-CN.md. Configured 20% HTTP 500 and 10% hang are not substituted for observations. |
| OCR timeout | One second for phase timeouts and the whole attempt. Successful local mock requests were measured in milliseconds, while default hangs last 600s. This is an assessment-local budget, not a claim about real vendors. |
| Retry limit | Six total attempts, exponential delay 0.1s capped at 0.5s. Network deadline/backoff budget 7.7s per case, excluding queue/SQLite/OS overhead. Four attempts cost at most 4.7s. Six is a conservative finite model-based choice; these three seeds did not show a single-round outcome improvement over four. |
| Exhausted retries | Visible ocr_failed with safe last error/code, cumulative applied attempt count and retryability. Normal process retries pending/transient failures with another finite budget; 400/422/invalid response and reviewed states are excluded. Prior run/attempt records remain in SQLite. |

## 3. Verification evidence

Provide command output, excerpts, or links to artifacts for each check.

### Q1 T1: import and persistence (2026-10-13)

Implemented multipart upload, batch details and filtered/paginated case lists.
The supplied CSV yields 43 accepted and 7 rejected rows, with physical line
numbers and field-specific reasons. Tests exercise malformed files, full import
rollback after an injected second-case storage failure, lock contention,
concurrent uploads and actual process restart with persisted data. See
`q1_api/tests/`, `q1_api/README.md`, and `WORK_LOG.zh-CN.md` for commands and outcomes.
This is not verification of review transitions, OCR, delivery or deployment.

### Q1 T2: review and module acceptance (2026-10-13)

Tests cover the 32 state/stage/decision combinations (4 legal, 28 conflicts),
normal two-stage approval, terminal rejection at either stage, repeated or
reversed decisions, malformed input, missing cases and concurrent reviews.
At each stage, both approve/approve and approve/reject simultaneous requests
produce one 200, one 409, and one matching audit record. Injecting a review
record insertion error rolls back the case change. Real localhost HTTP tests
verify approval, restart persistence and rejection of a repeated decision.

Q1 tests explicitly set OCR-completed state through a database fixture;
there is no production OCR bypass. Real OCR and export lifecycle remain Q2.
The module acceptance commands and exact results are recorded in
`q1_api/evidence/` and `WORK_LOG.zh-CN.md`.

### Q1 follow-up: approval edge cases (2026-10-13)

Added 45 checks, bringing the suite to 157 passing tests. Eight concurrency
scenarios repeated ten times passed (400 review requests); two real API processes
also serialized competing reviews correctly. Actual killed writer processes
verified rollback before commit and persistent state/no duplicate record after
commit. Update failures and a read lock blocking COMMIT also rolled back safely.
These drills do not simulate power loss, storage corruption or sustained load.

Found that default JSON parsing silently selected the final duplicate field.
Six regression tests failed before a review-only duplicate-field dependency
was added; duplicate stage/decision now returns 422 before any state change.
Other new tests verify existing behavior, not additional claimed RED cycles.
See `q1_api/evidence/Q1-REVIEW-EDGE-CASES.zh-CN.md` for the complete list and evidence.

Limits: no authentication/roles/two-person separation, actor/reason capture,
tamper-resistant audit log, request replay or versioned approval of edited data.
Returning a case to an earlier stage is intentionally unavailable under the
current terminal-rejection rules. Real OCR/export race checks remain Q2.

### OCR recovery

Implemented and verified for Q2.1/Q2.2. Original mock default-fault measurements
and actual API SIGKILL/restart comparisons cover seeds 42/7/99. All final
case_ref/full OCR object/status comparisons passed within the predefined maximum
of three recovery rounds. Seed 42 needed two clean/recovery rounds; seeds 7/99
needed one. Seed 42 had one readable exhaustion after either four or six attempts
in the single-round measurements; those failures remain in the evidence.

Tests also cover duplicate triggers, queued capacity, partial progress, terminal
errors, persistent transient failure, late success/failure after human approval,
cancelled SQLite threads, old run tokens, attempt/result atomicity, old-Q1 schema
migration, lifecycle cancellation and delayed error persistence after DB lock
recovery. A read-only review found cancelled trigger claims could become orphaned
running jobs; a failing regression preceded shielded claim/cleanup repair.

Only a single worker is supported. Completed run means the attempt budget ended,
not universal OCR success. In-flight calls killed before local persistence may be
absent from local attempt history; mock debug records provide the external view.
Disk outage recovery requires storage to return and a batch query/process (or
restart); this is not a distributed queue, power-loss or corruption guarantee.

### Account-opening submission

Implemented and verified. Exact approved membership, rollback, concurrent exports,
new approvals, immutable downloads, strict receipts, bounded retry and duplicate
deliver calls are covered. All three original-mock native scenarios passed:
full lifecycle creates two distinct exports (2 and 1 rows); response loss after
ingestion takes two requests but one ingestion; actual SIGKILL after remote
receipt storage and before local confirmation automatically recovers on restart,
also with two attempts and one ingestion. CSV bytes/hash match local and remote
receipts. No mock restart/reset occurs across recovery.

Defaults are a 1s whole-attempt deadline, four total attempts and 0.1/0.2/0.4s
backoff (4.7s network/backoff budget, excluding queue/storage overhead). Timeout,
5xx/429 and network failure retry; 400/invalid or mismatched receipt stops the
round. Exhausted/failed exports remain visible and require explicit deliver for
a new finite round; their original bytes/key never change. This is an assessment
budget, not a real-vendor SLA. The controlled lost-response experiment uses .1s
timeout against .5s response delay, and succeeds on its second request.

Graceful shutdown marks active delivery failed/retryable; explicit deliver resumes.
SIGKILL leaves delivering, which startup automatically requeues. Durable attempt
counts are intents recorded before I/O, not proof every request was sent. Old
threads cannot overwrite newer results. Database-lock deferral and local receipt
write failure also have dedicated recovery tests. A repeated cancellation test
exposed interrupted claim cleanup; idempotent close repaired it before acceptance.

Full suite: 411 passed locally and in a new Python 3.12.14 requirements-only
environment. Existing TestClient deprecation warning remains. Full evidence,
limits, native source fingerprints and reproduction commands are linked in
`q2_integration/evidence/Q2-ACCEPTANCE-20261013.md`.

The design supports one API worker. Mock deduplication lasts only as long as its
process; production idempotency retention and storage corruption are not validated.

### Deployment, persistence and health (Q3.1/Q3.2)

Implemented and verified on 2026-10-14. The API runs as UID10001 with one worker,
SQLite in a named /data volume, and nginx as the only published localhost port.
Compose health checks and service_healthy dependencies control startup. The
configured sample is imported atomically with a hash-identified marker, so
restarts preserve its batch ID, approvals and delivery receipts. A changed or
missing configured seed source fails startup rather than replacing records.

Liveness is separate from readiness. Readiness opens an existing database without
creating it, reads all eight non-seed workflow tables and performs a real write
that is rolled back, testing journal-directory access as well as the file. It
waits at most0.2s for a write lock. An independent review reproduced three omitted
tables reporting ready; failing tests preceded the correction. Missing/corrupt,
read-only and lock cases are tested, without business changes from the probe.

Full suites passed450 tests on macOS and450 on Linux/arm64 Python3.12.15 as the
non-root API user, each with the existing TestClient warning. Seven direct
requirements are pinned and reinstalled in the image. This is not a tested
Python3.10 environment or a full transitive/image-digest lock.

A standalone experiment in a fresh source snapshot/project/volume passed nine
stages. It verified seeded startup, real API/OCR nginx routing, default-fault
smoke, forced API IP replacement without nginx restart, and entire-stack restart
with all nine stored business tables unchanged. Three request-size boundaries,
DB/directory chmod failures (ready503/live200 and Docker unhealthy-to-healthy),
and a direct delayed OCR504 at5.07s with exactly one upstream request also passed.
The source and snapshot hashes stayed unchanged across the experiment; owned
test resources were cleaned up. See q3_deploy/evidence/T6-ACCEPTANCE-20261014.md
and linked raw report/SQL snapshots/commands for exact results and limits.

The smoke's default OCR run had37 successes and6 failures including5badscans;
it proves the selected successful case lifecycle, not that transient failures
never exhaust. A full mock restart erases its remote idempotency memory; that
restart test proves local persistence and no resend of already delivered exports,
not permanent downstream deduplication. Runtime unhealthy does not auto-repair
or restart services. nginx timeouts are idle timeouts, and DNS replacement may
briefly produce502. Backup/restoration are verified below; the earlier T6 results remain historical evidence.

### Backup retention

Implemented using SQLite's online backup API, validated staging files, a shared
operation lock and a separate backup volume. A successful snapshot is published
before rotation removes old owned files. A failed backup does not prune; UTC
microsecond timestamps plus random suffixes avoid accidental name reuse.
Restore requires a stopped API and preserves a safety copy before replacement.

Actual Docker execution created nine online backups and retained the newest
seven. Below are independent directory listings before/after the ninth run:

```text
Before ninth backup (7 files):
backup-20261014T021546554150Z-2604db31.sqlite3
backup-20261014T021547462249Z-7164b96d.sqlite3
backup-20261014T021548245087Z-9987fa4d.sqlite3
backup-20261014T021549509703Z-7adbb1bd.sqlite3
backup-20261014T021550553206Z-db053429.sqlite3
backup-20261014T021551377638Z-f80548b0.sqlite3
backup-20261014T021552140186Z-03ea8db8.sqlite3

After ninth backup (7 files):
backup-20261014T021547462249Z-7164b96d.sqlite3
backup-20261014T021548245087Z-9987fa4d.sqlite3
backup-20261014T021549509703Z-7adbb1bd.sqlite3
backup-20261014T021550553206Z-db053429.sqlite3
backup-20261014T021551377638Z-f80548b0.sqlite3
backup-20261014T021552140186Z-03ea8db8.sqlite3
backup-20261014T021552886860Z-7f77edc5.sqlite3
```

A post-commit clean-clone check exposed one flaky smoke test: its50ms HTTP
budget could expire on readiness before the intended OCR deadline check. The
failed run was retained and the test was changed to separate a controlled
workflow clock from real HTTP. Production code was unchanged; a follow-up test
fix preserves the original Q3 commit history.

The source remained live during backups; active WAL/uncommitted-write behavior
was tested separately against real SQLite. This is not a concurrency load test.
A later43/7 upload was rolled back by actual restore. All nine tables, including
two reviews, CSV BLOB, export identity/key, delivery attempts and receipt, matched
before restart, after restart and after three repeated deliver requests. Both
mock processes stayed alive and downstream ingestion remained exactly one.
A corrupt backup and a restore attempted while API was running were refused
without changing existing data. Safety copies are separate from seven-file
rotation and require operator cleanup after verification.

macOS and non-root Linux each passed478 tests; native complete/lost-response/
SIGKILL delivery scenarios passed again. The isolated Docker recovery experiment
passed nine stages, with57 source hashes unchanged and no cleanup errors. See
[q3_deploy/evidence/T7-ACCEPTANCE-20261014.md](q3_deploy/evidence/T7-ACCEPTANCE-20261014.md)
for raw command output, per-run listings, full database comparisons and limits.

Restoring an old snapshot cannot undo downstream ingestion. Exports absent from
that snapshot can be recreated with new identities, so operators must reconcile
post-snapshot deliveries before restart; saved pending/delivering exports resume
automatically. Backups on the same host are not off-host disaster protection.
No automatic schedule is installed; restore requires exclusive offline access.


## 4. AI-generated code requiring further review

Candidate understanding has not yet been personally verified. Codex generated
the Q1/Q2 implementation and Q3 deployment/seed/readiness/smoke/backup/recovery code in `q1_api/app/` and their tests.
Alex should explain the parse-before-write boundary, SQLite rollback/constraints,
CSV physical line numbering, the transition table, concurrent-review serialization,
atomic audit writes, HTTP error distinctions, asyncio task lifecycle, retry
budgets, run-token fencing, immutable export identity, unknown ingestion outcomes,
receipt verification, atomic seeding, Docker volumes, readiness versus liveness,
nginx routing/timeouts, online SQLite snapshots, retention, offline recovery
and post-snapshot delivery reconciliation before this section
can be finalized. This records pending review, not an assertion about what Alex
does or does not understand.

The assessment values accurate disclosure. The requirement to explain submitted code during the interview remains in effect.

## 5. Incomplete work and next steps

Q1 and all Q2 parts are implemented and verified. Isolated Q1 tests still use OCR
fixtures; Q2 native tests exercise the real mocks through delivery.
Q3 core deployment and backup/recovery are implemented and verified. The Q3
module and test-fix commits were verified at732b6ea in a clean clone:478 tests
and nine Docker restore stages passed, with exact-hash receipts saved locally.
The public personal fork and push are complete. CI has passed its first real
push run; the final documentation commit must also be verified at its own SHA.
Email submission and candidate review remain unfinished. The installed TestClient emits a
dependency deprecation warning; tests currently run successfully. The one-day
follow-up prioritization will be finalized against the completed submission.

## 6. Continuous integration

The CI bonus workflow runs the complete root-level pytest command and Docker
Compose build on every push, with separate jobs, bounded timeouts and no path
filters or ignored failures. Ubuntu24.04/Python3.12 is the tested target; tests
run as the hosted runner's ordinary user to preserve permission-test meaning.
Official checkout/setup-python actions use verified full SHAs, read-only
repository permission, and no retained checkout credentials. Compose defaults
are nonsecret, so CI needs no local.env. Build success alone is not runtime
acceptance; the Q3 deployment and restore experiments remain separate evidence.

The first actual push run38501842241 passed atb6d07e5 on the personal public
fork:478 tests in40.43s (one existing warning) and all three application-image
builds succeeded. Raw metadata/logs are inq3_deploy/evidence/ci-first-run-20261014.*.
See [CI acceptance](q3_deploy/evidence/CI-ACCEPTANCE-20261014.md). The documentation
commit creates a new SHA and must pass too; its final run URL/receipt will be
recorded separately instead of treating the earlier green run as final proof.
