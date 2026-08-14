#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
SCRIPT_DIR="$(pwd)"

source "$SCRIPT_DIR/sync-deps.sh"

REAL_HOME="${REAL_HOME:-$(getent passwd "$(whoami)" | cut -d: -f6)}"
export REAL_HOME

if [ -z "${CODEX_BINARY:-}" ]; then
  CODEX_ENTRYPOINT="$(command -v codex || true)"
  if [ -z "$CODEX_ENTRYPOINT" ]; then
    echo "Error: codex CLI is not installed or is not on PATH" >&2
    exit 1
  fi

  CODEX_ENTRYPOINT="$(readlink -f "$CODEX_ENTRYPOINT")"
  CODEX_PACKAGE_ROOT="$(cd "$(dirname "$CODEX_ENTRYPOINT")/.." && pwd)"

  case "$(uname -m)" in
    x86_64)
      CODEX_PLATFORM_PACKAGE="codex-linux-x64"
      CODEX_TARGET="x86_64-unknown-linux-musl"
      ;;
    aarch64|arm64)
      CODEX_PLATFORM_PACKAGE="codex-linux-arm64"
      CODEX_TARGET="aarch64-unknown-linux-musl"
      ;;
    *)
      echo "Error: unsupported Codex host architecture: $(uname -m)" >&2
      exit 1
      ;;
  esac

  CODEX_BINARY="$CODEX_PACKAGE_ROOT/node_modules/@openai/$CODEX_PLATFORM_PACKAGE/vendor/$CODEX_TARGET/bin/codex"
fi

if [ ! -f "$CODEX_BINARY" ] || [ ! -x "$CODEX_BINARY" ]; then
  echo "Error: executable Codex binary not found at $CODEX_BINARY" >&2
  echo "Set CODEX_BINARY to the native Codex executable and retry." >&2
  exit 1
fi
export CODEX_BINARY

CODEX_AUTH_FILE="${CODEX_AUTH_FILE:-$REAL_HOME/.codex/auth.json}"
if [ ! -f "$CODEX_AUTH_FILE" ]; then
  echo "Error: Codex authentication file not found at $CODEX_AUTH_FILE" >&2
  exit 1
fi
export CODEX_AUTH_FILE

docker compose \
  -f docker-compose.yml \
  -f docker-compose.build.yml \
  -f docker-compose.codex.yml \
  up --build -d "$@"

echo "Marginalia: http://localhost:${MARGINALIA_PORT:-8000}"
echo "Provider ownership: Agent Governor daemon (codex CLI)"
