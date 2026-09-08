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

AG_NG_CANDIDATE="${MARGINALIA_AG_NG_SOURCE_DIR:-$SCRIPT_DIR/../../ag_ng}"
DOCKET_CANDIDATE="${MARGINALIA_DOCKET_SOURCE_DIR:-$SCRIPT_DIR/../../docket}"
MODEL_EXECUTION_CANDIDATE="${MARGINALIA_MODEL_EXECUTION_SOURCE_DIR:-$SCRIPT_DIR/../../model-execution}"
AG_NG_DIR="$(cd "$AG_NG_CANDIDATE" && pwd)"
DOCKET_DIR="$(cd "$DOCKET_CANDIDATE" && pwd)"
MODEL_EXECUTION_DIR="$(cd "$MODEL_EXECUTION_CANDIDATE" && pwd)"
EXPECTED_AG_NG_COMMIT="$(tr -d '[:space:]' < "$SCRIPT_DIR/AG_NG_CONTRACT_COMMIT")"
EXPECTED_DOCKET_COMMIT="$(tr -d '[:space:]' < "$SCRIPT_DIR/DOCKET_CONTRACT_COMMIT")"
EXPECTED_MODEL_EXECUTION_COMMIT="$(tr -d '[:space:]' < "$SCRIPT_DIR/MODEL_EXECUTION_CONTRACT_COMMIT")"

export_exact_tree() {
  local label="$1"
  local source_dir="$2"
  local commit="$3"
  local destination="$4"
  local marker="$5"
  local temporary

  if ! git -C "$source_dir" cat-file -e "$commit^{commit}" 2>/dev/null; then
    echo "Error: $label source at $source_dir does not contain required commit $commit" >&2
    echo "Fetch that exact commit without changing its working tree, then retry." >&2
    exit 1
  fi
  temporary="$(mktemp -d "$SCRIPT_DIR/.sync-${label}.XXXXXX")"
  git -C "$source_dir" archive "$commit" | tar -x -C "$temporary"
  printf '%s\n' "$commit" > "$temporary/$marker"
  rm -rf "$destination"
  mv "$temporary" "$destination"
  echo "Exported exact $label tree $commit from $source_dir"
}

# The standalone, read-only receipt-v1 compatibility reader is vendored in
# this repository. No source, build, or test step depends on classic AG.
test -f "$SCRIPT_DIR/receipt-v1/src/receipt_v1/__init__.py"

# ── ag-ng + Docket runtime ───────────────────────────────────────────────
# Export the pinned objects rather than copying either checkout. Their current
# branches and uncommitted campaign work are deliberately irrelevant.
export_exact_tree \
  ag-ng "$AG_NG_DIR" "$EXPECTED_AG_NG_COMMIT" \
  "$SCRIPT_DIR/ag-ng" AG_NG_CONTRACT_COMMIT
export_exact_tree \
  docket "$DOCKET_DIR" "$EXPECTED_DOCKET_COMMIT" \
  "$SCRIPT_DIR/docket-runtime" DOCKET_CONTRACT_COMMIT
export_exact_tree \
  model-execution "$MODEL_EXECUTION_DIR" "$EXPECTED_MODEL_EXECUTION_COMMIT" \
  "$SCRIPT_DIR/model-execution" MODEL_EXECUTION_CONTRACT_COMMIT

# ── Add new local deps above this line ────────────────────────────────────
