# Implementation reflection

Complete each section before submission. Replace bracketed prompts with decisions, measurements, or evidence. Use `Not completed` or `Not measured` where applicable, and explain the gap. Reviewers assess this record alongside the code.

## 1. Design decisions

| Decision | Rationale |
|---|---|
| Database | SQLite via Python's standard library. At T1, batches and cases are stored in a single file; import metadata, rejected-row details and accepted cases commit in one short transaction. This keeps native startup simple and provides atomic import behavior at the exercise's scale. Foreign keys, required values and per-batch reference uniqueness are enforced in storage. One-second lock waits bound contention; temporary unavailability is reported as 503. |
| Background OCR processing | Not completed: Q2 background processing and recovery are planned but not implemented. |
| Review state machine | `q1_api/app/workflow.py` holds four explicit transitions keyed by current status, stage and decision; every other transition is rejected with 409. `stage2_rejected` makes rejection at either stage terminal. SQLite `BEGIN IMMEDIATE` plus an old-status conditional UPDATE prevent competing decisions from both succeeding. The case change and append-only review record are one transaction; record insertion failure rolls back the status change. |
| Delivery idempotency key | Not completed: Q2 export/delivery has not been implemented. |

## 2. Mock observations and retry configuration

| Item | Measurement or decision |
|---|---|
| Observed OCR failure rates | Not measured for the application yet. The earlier contract-only diagnostic is not a Q2 failure-rate assessment. |
| OCR timeout | Not selected: requires Q2 measurements. |
| Retry limit | Not selected: requires Q2 measurements. |
| Exhausted retries | Not implemented: Q2. |

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

Not completed: Q2 OCR recovery has not been implemented or validated.

### Account-opening submission

Not completed: Q2 application delivery has not been implemented or validated.

### Backup retention

Not completed: Q3 backup and restoration have not been implemented or validated.

## 4. AI-generated code requiring further review

Candidate understanding has not yet been personally verified. Codex generated
the Q1 implementation in `q1_api/app/{main,models,imports,db,workflow}.py` and its tests.
Alex should explain the parse-before-write boundary, SQLite rollback/constraints,
CSV physical line numbering, the transition table, concurrent-review serialization,
atomic audit writes, and HTTP error distinctions before this section
can be finalized. This records pending review, not an assertion about what Alex
does or does not understand.

The assessment values accurate disclosure. The requirement to explain submitted code during the interview remains in effect.

## 5. Incomplete work and next steps

Q1 import/query and two-stage review are implemented. The Q1-scoped lifecycle
uses a database fixture for OCR completion. Q2 OCR/recovery/export/delivery,
Q3 deployment/readiness/seeding/backup/restore, CI, final full-system clean-clone validation
and candidate review remain unfinished. The installed TestClient emits a
dependency deprecation warning; tests currently run successfully. The one-day
follow-up prioritization will be finalized against the completed submission.
