#!/usr/bin/env bash
# The online SQLite helper and Python runtime live in the API image.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec docker compose exec -T api python /srv/scripts/database_backup.py backup "$@"
