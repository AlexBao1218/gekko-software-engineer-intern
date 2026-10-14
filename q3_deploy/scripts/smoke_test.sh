#!/usr/bin/env bash
# Run inside the API container so host Python/curl dependencies are unnecessary.
# BASE is resolved inside the container; DOCKER_CONTEXT is inherited unchanged.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec docker compose exec -T api python /srv/scripts/smoke.py --base "${BASE:-http://nginx}" "$@"
