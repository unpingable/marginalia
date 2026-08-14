#!/usr/bin/env bash
# Sync local-only dependencies into Docker build context.
#
# Both start.sh and start-codex.sh source this file. Add new non-PyPI
# dependencies here — one place, both launch paths.
#
# Usage: ./sync-deps.sh, or source it from a start script.

set -euo pipefail

if [ -z "${SCRIPT_DIR:-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

AGENT_GOV_CANDIDATE="${MARGINALIA_AG_SOURCE_DIR:-$SCRIPT_DIR/../../agent_gov}"
AGENT_GOV_DIR="$(cd "$AGENT_GOV_CANDIDATE" && pwd)"
EXPECTED_AG_COMMIT="$(tr -d '[:space:]' < "$SCRIPT_DIR/AG_CONTRACT_COMMIT")"

# ── agent-governor ────────────────────────────────────────────────────────
if [ ! -d "$AGENT_GOV_DIR/src/governor" ]; then
  echo "Error: agent-governor source not found at $AGENT_GOV_DIR"
  echo "Expected: ../../agent_gov relative to this repo"
  exit 1
fi
ACTUAL_AG_COMMIT="$(git -C "$AGENT_GOV_DIR" rev-parse HEAD)"
if [ "$ACTUAL_AG_COMMIT" != "$EXPECTED_AG_COMMIT" ]; then
  echo "Error: Marginalia requires AG commit $EXPECTED_AG_COMMIT"
  echo "Found: $ACTUAL_AG_COMMIT at $AGENT_GOV_DIR"
  exit 1
fi
rm -rf "$SCRIPT_DIR/agent-governor"
mkdir -p "$SCRIPT_DIR/agent-governor"
cp "$AGENT_GOV_DIR/pyproject.toml" "$SCRIPT_DIR/agent-governor/"
cp "$AGENT_GOV_DIR/README.md" "$SCRIPT_DIR/agent-governor/"
cp "$AGENT_GOV_DIR/LICENSE" "$AGENT_GOV_DIR/NOTICE" "$SCRIPT_DIR/agent-governor/"
cp -r "$AGENT_GOV_DIR/src" "$SCRIPT_DIR/agent-governor/"
cp "$SCRIPT_DIR/AG_CONTRACT_COMMIT" "$SCRIPT_DIR/agent-governor/"
for REQUIRED_PACKAGE in governor fiction_governor nonfiction_governor ops_governor; do
  if [ ! -d "$SCRIPT_DIR/agent-governor/src/$REQUIRED_PACKAGE" ]; then
    echo "Error: qualified agent-governor source omits $REQUIRED_PACKAGE" >&2
    exit 1
  fi
done
echo "Synced qualified agent-governor $ACTUAL_AG_COMMIT from $AGENT_GOV_DIR"

# ── receipt-kernel ────────────────────────────────────────────────────────
RECEIPT_KERNEL_DIR="$AGENT_GOV_DIR/libs/receipt_kernel"
if [ ! -d "$RECEIPT_KERNEL_DIR/src/receipt_kernel" ]; then
  echo "Error: receipt_kernel source not found at $RECEIPT_KERNEL_DIR" >&2
  exit 1
fi
rm -rf "$SCRIPT_DIR/receipt-kernel"
mkdir -p "$SCRIPT_DIR/receipt-kernel/src"
cp "$RECEIPT_KERNEL_DIR/pyproject.toml" "$RECEIPT_KERNEL_DIR/README.md" \
  "$SCRIPT_DIR/receipt-kernel/"
cp -r "$RECEIPT_KERNEL_DIR/src/receipt_kernel" "$SCRIPT_DIR/receipt-kernel/src/"
echo "Synced receipt-kernel from $RECEIPT_KERNEL_DIR"

# ── receipt-v1 ────────────────────────────────────────────────────────────
RECEIPT_V1_DIR="$AGENT_GOV_DIR/libs/receipt_v1"
if [ -d "$RECEIPT_V1_DIR/src/receipt_v1" ]; then
  rm -rf "$SCRIPT_DIR/receipt-v1"
  mkdir -p "$SCRIPT_DIR/receipt-v1/src"
  cp "$RECEIPT_V1_DIR/pyproject.toml" "$SCRIPT_DIR/receipt-v1/"
  cp -r "$RECEIPT_V1_DIR/src/receipt_v1" "$SCRIPT_DIR/receipt-v1/src/"
  echo "Synced receipt-v1 from $RECEIPT_V1_DIR"
else
  echo "Error: receipt_v1 source not found at $RECEIPT_V1_DIR" >&2
  exit 1
fi

# ── Add new local deps above this line ────────────────────────────────────
