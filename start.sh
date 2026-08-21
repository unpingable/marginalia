#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
SCRIPT_DIR="$(pwd)"

source "$SCRIPT_DIR/sync-deps.sh"

export MARGINALIA_BUILD_SHA="${MARGINALIA_BUILD_SHA:-$(git rev-parse HEAD)}"
export MARGINALIA_BUILD_TIME="${MARGINALIA_BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
export MARGINALIA_IMAGE_REF="${MARGINALIA_IMAGE_REF:-marginalia:local}"
export MARGINALIA_DEPLOYMENT_ID="${MARGINALIA_DEPLOYMENT_ID:-compose-${HOSTNAME:-host}}"

source "$SCRIPT_DIR/compose-files.sh"
COMPOSE_FILES+=(-f docker-compose.build.yml)

docker compose "${COMPOSE_FILES[@]}" up --build -d "$@"

echo "Marginalia: http://localhost:${MARGINALIA_PORT:-8000}"
echo "Provider ownership: Agent Governor daemon (BACKEND_TYPE=${BACKEND_TYPE:-anthropic})"
