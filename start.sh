#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
SCRIPT_DIR="$(pwd)"

source "$SCRIPT_DIR/sync-deps.sh"

docker compose \
  -f docker-compose.yml \
  -f docker-compose.build.yml \
  up --build -d "$@"

echo "Marginalia: http://localhost:${MARGINALIA_PORT:-8000}"
echo "Provider ownership: Agent Governor daemon (BACKEND_TYPE=${BACKEND_TYPE:-anthropic})"
