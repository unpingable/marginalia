#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
SCRIPT_DIR="$(pwd)"

source "$SCRIPT_DIR/sync-deps.sh"

REAL_HOME="${REAL_HOME:-$(getent passwd "$(whoami)" | cut -d: -f6)}"
export REAL_HOME

docker compose \
  -f docker-compose.yml \
  -f docker-compose.build.yml \
  -f docker-compose.codex.yml \
  up --build -d "$@"

echo "Marginalia: http://localhost:${MARGINALIA_PORT:-8000}"
echo "Provider ownership: Agent Governor daemon (codex CLI)"
