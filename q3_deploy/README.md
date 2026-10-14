# Module 3: Deployment and operations

Deployment, persistent storage, seeded startup, readiness and the end-to-end
smoke script are implemented. Q3.3 adds online SQLite backups and an offline
restore procedure; see the acceptance evidence linked below for actual results.

## Configure and start

From `q3_deploy/`:

```bash
cp .env.example .env
# Adjust local values as needed; never commit .env.
docker compose up --build
```

This repository uses SQLite, so no separate database service is required. The
fixed services are `api`, `mock_ocr`, `mock_onboarding`, and `nginx`. The API runs
one Uvicorn worker as UID/GID 10001, with the `api_data` named volume at `/data`
and independent `api_backups` named volume at `/backups`.
Only nginx publishes a host port, bound to localhost (default 8080). Keep
`DATABASE_PATH` inside `/data` for persistence. Changing Compose project names
selects a different volume; `docker compose down` retains it, while `down -v`
removes it. Do not use the latter on data you need.

On this development Mac, use the existing dedicated context for each command
(`docker --context colima-gekko ...`) or set `DOCKER_CONTEXT=colima-gekko` in the
current terminal. Other machines use their working Docker context. No global
context/Python change is needed.

Wait for readiness in another terminal, then run the smoke:

```bash
curl -f http://localhost:8080/api/ready
bash scripts/smoke_test.sh
```

For scripted startup, `docker compose up --build -d --wait` observes service
health. Compose uses `condition: service_healthy`, not fixed startup sleeps.
The API waits for both mock `/online` checks; nginx waits for API readiness and
OCR health. Startup dependencies do not automatically repair a later outage.

## Seed and persistent data

The deployment sets `SEED_CSV_PATH=/srv/data/account_applications.csv`. First
startup imports the supplied 50 rows as 43 accepted and 7 rejected. A seed marker,
its SHA-256 and the whole batch commit atomically. Restart returns the original
batch ID without overwriting reviews, OCR, exports or delivery receipts. A
changed/missing/invalid configured source fails startup explicitly. The native
API remains unseeded when `SEED_CSV_PATH` is unset.

Readiness includes `seed_batch_id` when seeding is enabled; retrieve it using
`GET /api/batches/{id}`. Seeding imports only; it does not run OCR or approve
cases. The smoke uploads a **different** batch, so the original seed stays intact.
It performs OCR, two reviews, export, download/hash verification, empty repeat
export, receipt validation and a check of exactly one downstream ingestion.
It uses the original mock failure settings by default; unreadable/exhausted OCR
cases may remain failed, and are not falsely reported as approved.

The shell launcher uses `docker compose exec -T api` and the Python standard
library inside the image, so the host needs Docker and Bash rather than a
separate Python environment. `BASE` defaults to `http://nginx` as viewed inside
the container. CLI options are forwarded, for example:

```bash
bash scripts/smoke_test.sh --help
bash scripts/smoke_test.sh --ocr-timeout 400 --delivery-timeout 30
```

The final stdout line is JSON with `pass`, seed/new batch IDs, chosen case,
export ID, SHA and receipt. Failure exits nonzero with the failed step on stderr.
Polling and HTTP requests are bounded. It never resets mocks or deletes data.

## Liveness and readiness

- `GET /api/health`: 200 `{"status":"ok"}` while the application can respond.
- `GET /api/ready`: 200 only after initialization/seed and when the **existing**
  database can be opened, workflow tables read, and a real journaled write rolled
  back. A missing DB is not created by the probe. Failure returns 503 with a safe
  message. The probe does not alter business records or leave a probe table.

Readiness takes a short SQLite write lock (up to 0.2s wait). A sustained writer
or read-only database/directory can make it temporarily unready while health
remains 200. Docker checks every 5s, marking unhealthy after six failed checks;
that state does not by itself restart the container. Repair the cause and the
next successful probe restores healthy. A healthy DB does not prove an external
vendor is reachable; inspect OCR/delivery state and mock health separately.

## nginx routing and boundaries

`/api/*` and `/ocr/*` retain the original request path and query. Docker DNS is
refreshed every 5s so replacement API IPs are resolved without restarting nginx;
requests during replacement may briefly return 502. POSTs are not automatically
replayed by nginx. API OCR/delivery retries have their separate persisted rules.

`client_max_body_size 3m` allows multipart overhead around the API's **2 MiB CSV**
limit. A CSV over 2 MiB but below nginx's body limit gets JSON 413 from the API;
a body over 3 MiB gets nginx's HTML 413. Files over nginx's old 1 MiB default can
reach application validation. Invalid CSV still returns the application's 400.

Connect timeout is 2s; proxy read/send idle timeouts are 5s. A direct delayed
`/ocr/parse` request therefore returns 504 around 5s, while API `process` returns
202 immediately and handles OCR asynchronously with its own deadline. These
proxy timeouts are inactivity limits, not a whole-response SLA.

## Configuration and verification

`.env.example` lists database/seed paths, port, all OCR/delivery endpoints and
finite retry settings, and original mock fault knobs. The onboarding client uses
**DELIVERY_URL**, replacing the old stub's unused ONBOARDING_URL. Credentials are
not needed for these local mocks. API settings retain the Q2 documented defaults.

Seven directly used dependencies are pinned in the root `requirements.txt`.
The Docker build context allowlist admits app sources, the sample, requirements,
Dockerfile and operational scripts; .env, local databases, .git, environments,
tests and collected evidence are excluded. The API image is not a dev checkout.

```bash
# Repository root, native unit/integration suite:
python -m pytest q1_api/
# Dedicated fresh snapshot/project/volume, automatic owned-resource cleanup:
python q1_api/tests/deploy_experiment.py --context colima-gekko --output /tmp/gekko-q3-new
```

Use a new output directory. The deployment experiment requires Docker Compose
and the project's Python dependencies. It records actual startup/health,
nginx routes/limits/timeouts, changed container IP resolution, database failure,
and preserved business data after recreation. Backup verification is separate.
See [implementation/process](../Q3_DEPLOYMENT_IMPLEMENTATION.zh-CN.md) and
[runbook](RUNBOOK.md).

## Online backups and recovery

Run `bash scripts/backup.sh` while the API is running. The container's stdlib
SQLite backup API captures committed data (including WAL); it does not copy a
live database file directly. A temporary snapshot must pass integrity, foreign
key and application-schema checks before publication. Names contain UTC time
and a random suffix; rotation retains the seven newest recognized regular
`backup-*.sqlite3` files. Other files, temporary files and symlinks are excluded.
An operation lock serializes backup/rotation/restore and concurrent operations
fail explicitly. Failed copies do not prune existing backups.

`BACKUP_DIR` defaults to `/backups`; keep it inside that mounted volume.
`BACKUP_TIMEOUT_SECONDS` defaults to30 and bounds SQLite copy/validation work,
including a busy database. This is not a hard operating-system I/O deadline.
The JSON result includes the new basename and before/after rotation lists.
No scheduler is installed; invoke manually or arrange scheduling separately.

Follow [RUNBOOK.md](RUNBOOK.md) to stop only API, restore a chosen basename with
`bash scripts/restore.sh BACKUP_BASENAME`, and restart API. Restore first
validates the snapshot and stages a complete replacement. It preserves an
existing valid DB in `/backups/pre-restore/`; these safety copies are outside
routine rotation and need deliberate cleanup after recovery verification.
An active API is refused; operators must also prevent other writers or a
concurrent manual restart. Backup/restore are Unix-container operations.

Recovery returns local data to the snapshot time. It cannot undo external
ingestions: reconcile post-snapshot exports before restarting, and preserve
onboarding's in-memory idempotency registry. A separate backup volume is not
protection from loss of the Docker host; copy backups off-host when needed.

```bash
python q1_api/tests/backup_experiment.py --context colima-gekko --output /tmp/gekko-backup-new
```

Run that command from the repository root with the project's dependencies. It
uses an isolated project and disposable volumes, drives a real reviewed and
delivered export, creates nine online backups, verifies seven-file retention,
restores after a later mutation, compares every row/BLOB in nine tables, then
checks HTTP status/content/receipt and downstream ingestion after restart.
It also exercises invalid restore refusal and preserves raw evidence on failure.
See [Q3.3 acceptance](evidence/T7-ACCEPTANCE-20261014.md).
