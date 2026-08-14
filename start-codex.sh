#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
SCRIPT_DIR="$(pwd)"

source "$SCRIPT_DIR/sync-deps.sh"

docker compose \
  -f docker-compose.yml \
  -f docker-compose.build.yml \
  -f docker-compose.codex.yml build

MARGINALIA_IMAGE=marginalia:local \
MARGINALIA_CODEX_VOLUME="${COMPOSE_PROJECT_NAME:-$(basename "$SCRIPT_DIR")}_marginalia_codex_home" \
MARGINALIA_SKIP_PULL=1 \
"$SCRIPT_DIR/marginalia" login --no-pull

docker compose \
  -f docker-compose.yml \
  -f docker-compose.build.yml \
  -f docker-compose.codex.yml \
  up -d "$@"

echo "Marginalia: http://localhost:${MARGINALIA_PORT:-8000}"
echo "Provider ownership: Agent Governor daemon (codex CLI)"
