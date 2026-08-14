#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT="${MARGINALIA_DATA_ROOT:-/data}"
DAEMON_DIR="$DATA_ROOT/.governor"
CONTEXT_ID="${GOVERNOR_CONTEXT_ID:-marginalia}"
MODE="${GOVERNOR_MODE:-fiction}"

# GovernorContextManager must use the daemon's governor directory as its base.
# Refuse an ambiguous split-brain configuration instead of silently starting.
if [ -n "${GOVERNOR_DAEMON_DIR:-}" ] && [ "$GOVERNOR_DAEMON_DIR" != "$DAEMON_DIR" ]; then
    echo "GOVERNOR_DAEMON_DIR must equal $DAEMON_DIR" >&2
    exit 1
fi
if [ -n "${GOVERNOR_CONTEXTS_DIR:-}" ] && [ "$GOVERNOR_CONTEXTS_DIR" != "$DAEMON_DIR" ]; then
    echo "GOVERNOR_CONTEXTS_DIR must equal $DAEMON_DIR" >&2
    exit 1
fi

mkdir -p "$DATA_ROOT"
export MARGINALIA_DATA_ROOT="$DATA_ROOT"
export GOVERNOR_DAEMON_DIR="$DAEMON_DIR"
export GOVERNOR_CONTEXTS_DIR="$DAEMON_DIR"
export GOVERNOR_CONTEXT_ID="$CONTEXT_ID"
export GOVERNOR_MODE="$MODE"

SOCKET_PATH="${GOVERNOR_SOCKET:-$(python3 -c \
    'from pathlib import Path; from gov_webui.daemon_client import default_socket_path; import sys; print(default_socket_path(Path(sys.argv[1])))' \
    "$DAEMON_DIR")}"
export GOVERNOR_SOCKET="$SOCKET_PATH"

echo "Starting Agent Governor for Marginalia"
echo "  root:    $DATA_ROOT"
echo "  context: $CONTEXT_ID"
echo "  mode:    $MODE"
echo "  socket:  $SOCKET_PATH"

governor --root "$DATA_ROOT" serve --socket "$SOCKET_PATH" --mode "$MODE" &
DAEMON_PID=$!
APP_PID=""

cleanup() {
    if [ -n "$APP_PID" ]; then
        kill "$APP_PID" 2>/dev/null || true
    fi
    kill "$DAEMON_PID" 2>/dev/null || true
    wait "$DAEMON_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _attempt in $(seq 1 100); do
    if [ -S "$SOCKET_PATH" ]; then
        break
    fi
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
        echo "Agent Governor exited before creating its socket" >&2
        exit 1
    fi
    sleep 0.1
done

if [ ! -S "$SOCKET_PATH" ]; then
    echo "Agent Governor socket was not ready after 10 seconds" >&2
    exit 1
fi

uvicorn gov_webui.adapter:app --host 0.0.0.0 --port 8000 &
APP_PID=$!
wait "$APP_PID"
