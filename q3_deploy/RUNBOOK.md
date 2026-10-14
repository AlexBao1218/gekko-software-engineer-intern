# Operations runbook

Run from `q3_deploy/`, using the same Compose project throughout. On this Mac:
`export DOCKER_CONTEXT=colima-gekko`. Keep one API worker. Commands below use
port8080; substitute your configured NGINX_PORT.

## 1. Start and check

```bash
cp .env.example .env             # first setup only; retain existing local edits
# Keep DATABASE_PATH under /data and BACKUP_DIR under /backups.
docker compose up --build        # foreground; automation: up --build -d --wait
```

In another terminal: `curl -f http://localhost:8080/api/ready`, then
`bash scripts/smoke_test.sh`. `docker compose ps` should show four healthy
services. Ready includes the preserved seed_batch_id; smoke creates a separate
batch and prints `"pass":true` with its export SHA and receipt. `down` preserves
volumes; **never use `down -v` on needed data or backups**. Changing project names
selects different volumes. A changed/missing seed source fails startup.

## 2. Logs and database health

`docker compose logs --tail=100 api mock_ocr mock_onboarding nginx` (add `-f` to
follow). Live200 at `/api/health` plus ready503 at `/api/ready` indicates a DB
probe failure. Check logs, configured path, competing writers and permissions:
`docker compose exec api sh -c 'id; ls -ld /data /backups; ls -l /data'`.
UID/GID10001 needs the database AND its directory writable. Repair the cause;
do not delete the DB to clear an alert. A succeeding probe restores healthy;
health checks do not automatically repair or restart failed services.

## 3. OCR and delivery diagnosis

```bash
docker compose exec -T mock_ocr python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:9100/online',timeout=2).read().decode())"
curl -f 'http://localhost:8080/api/batches/BATCH_ID/cases?status=ocr_failed&page_size=100'
curl -f -X POST http://localhost:8080/api/batches/BATCH_ID/process
```

Inspect saved reason/retryability first. Transient failures get a new bounded
run; badscan needs corrected input. Reviewed/exported cases are preserved.
Direct OCR proxy504 at about5s is nginx's timeout; background OCR uses its own1s
deadline. Restart a stopped OCR service with `docker compose up -d mock_ocr`.
For delivery inspect `/api/batches/BATCH_ID/exports` and `/api/exports/EXPORT_ID`;
retry a failed saved export via `POST /api/exports/EXPORT_ID/deliver`, using its
existing identity. Preserve onboarding: its deduplication registry is in memory.

## 4. Online backup and offline restore

```bash
bash scripts/backup.sh                    # API stays running; prints JSON path and retention lists
docker compose exec -T api ls -1 /backups  # newest seven backup-*.sqlite3 snapshots
# Optional off-host copy: mkdir -p backups; docker compose cp api:/backups/. ./backups/
```

Choose an exact basename from the successful backup output. Before restarting a
restored DB, reconcile deliveries after that snapshot with the downstream service:
local rollback does not undo ingestion, and an export missing from the snapshot
could otherwise receive a new key. Startup retries saved pending/delivering
exports automatically. Keep onboarding running to preserve its existing keys.

```bash
docker compose stop api
bash scripts/restore.sh 'BACKUP_BASENAME.sqlite3'
docker compose up -d --no-deps --wait api
curl -f http://localhost:8080/api/ready
curl -f http://localhost:8080/api/batches/BATCH_ID
curl -f http://localhost:8080/api/exports/EXPORT_ID
curl -f http://localhost:8080/api/exports/EXPORT_ID/download -o /tmp/restored-export.csv
```

Restore refuses an active API; prevent anyone concurrently starting another
writer. Invalid backups leave the current DB intact. Existing valid data gets a
safety snapshot in `/backups/pre-restore/`, outside seven-backup rotation; retain
it until recovery is checked, then remove deliberately. If the current DB is
corrupt and cannot be preserved, leave API stopped and quarantine the DB together
with any `-wal`, `-shm`, `-journal` files before retrying. Confirm the original
seed ID, approvals, export SHA/key/receipt and no repeat ingestion; do not run
smoke as a substitute because smoke creates new records. Backups on a separate
volume protect against replacing the data volume, not host/disk loss; copy them
off-host for that protection. No automatic backup schedule is configured.
