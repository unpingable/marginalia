#!/usr/bin/env bash
# Translate Marginalia's stable provider alias into Codex CLI default-model
# selection. Explicit future model ids continue through unchanged.

set -euo pipefail

CODEX_NATIVE_PATH="${CODEX_NATIVE_PATH:-/opt/codex/codex}"
if [ ! -x "$CODEX_NATIVE_PATH" ]; then
  echo "Codex executable is unavailable at $CODEX_NATIVE_PATH" >&2
  exit 127
fi

ARGS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    -m|--model)
      if [ "$#" -lt 2 ]; then
        echo "$1 requires a model value" >&2
        exit 2
      fi
      if [ "$2" != "codex-default" ]; then
        ARGS+=("$1" "$2")
      fi
      shift 2
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

exec "$CODEX_NATIVE_PATH" "${ARGS[@]}"
