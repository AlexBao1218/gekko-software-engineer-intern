#!/usr/bin/env bash
# Offline restore: keep all writers stopped until this command finishes.
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo 'Usage: restore.sh BACKUP_BASENAME' >&2
  exit 1
fi
cd "$(dirname "${BASH_SOURCE[0]}")/.."
containers=$(docker compose ps --all -q api)
if [[ -z "$containers" ]]; then
  echo 'Restore refused: API container not found; confirm the Compose project and create/stop API first.' >&2
  exit 1
fi
for container in $containers; do
  state=$(docker inspect --format '{{.State.Running}} {{.State.Paused}} {{.State.Restarting}}' "$container")
  if [[ "$state" != 'false false false' ]]; then
    echo 'Restore refused: API must be stopped (not running, paused or restarting).' >&2
    exit 1
  fi
done
exec docker compose run --rm --no-deps api python /srv/scripts/database_backup.py restore "$1"
