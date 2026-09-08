#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT="${MARGINALIA_DATA_ROOT:-/data}"
CONTEXT_ID="${GOVERNOR_CONTEXT_ID:-marginalia}"
MODE="${GOVERNOR_MODE:-fiction}"
INPUT_ROOT="${MARGINALIA_INPUT_ROOT:-/run/marginalia-input}"

install_private_file() {
    source_path="$1"
    destination="$2"
    label="$3"
    if [ ! -f "$source_path" ] || [ -L "$source_path" ]; then
        echo "Refusing missing or non-regular $label: $source_path" >&2
        exit 1
    fi
    mode="$(stat -c '%a' "$source_path")"
    if (( (8#$mode & 8#077) != 0 )); then
        echo "Refusing $label with group/world permissions ($mode): $source_path" >&2
        exit 1
    fi
    install -D -o root -g root -m 0600 "$source_path" "$destination"
}

if [ "$MODE" != "fiction" ]; then
    echo "Marginalia is fiction-only; GOVERNOR_MODE must be fiction" >&2
    exit 1
fi

mkdir -p "$DATA_ROOT"
export MARGINALIA_DATA_ROOT="$DATA_ROOT"
export MARGINALIA_CONTEXTS_DIR="${MARGINALIA_CONTEXTS_DIR:-$DATA_ROOT/.marginalia/contexts}"
export MARGINALIA_AG_NG_ONLY=true

# Bind-mounted NAS files commonly retain a non-root host UID.  Copy the two
# inputs this process may read into its own root-owned container filesystem.
# The web process never receives provider RPC or provider API credentials.
install_private_file \
    "$INPUT_ROOT/config/providers.json" \
    /etc/marginalia/providers.json \
    "model catalog"
install_private_file \
    "$INPUT_ROOT/web-secrets/marginalia-evidence-keys.json" \
    /run/secrets/marginalia-evidence-keys.json \
    "evidence keyring"
export MARGINALIA_MODEL_CONFIG=/etc/marginalia/providers.json
export MARGINALIA_EVIDENCE_KEY_FILE=/run/secrets/marginalia-evidence-keys.json

echo "Migrating Marginalia state layout"
python3 -m gov_webui.state_layout migrate --data-root "$DATA_ROOT"

echo "Checking Marginalia durable schemas"
python3 -m gov_webui.ops --data-root "$DATA_ROOT" --context-id "$CONTEXT_ID" preflight --apply-migrations

echo "Starting Marginalia with ag-ng-only dispatch"
echo "  root:     $DATA_ROOT"
echo "  contexts: $MARGINALIA_CONTEXTS_DIR"
echo "  context:  $CONTEXT_ID"

exec uvicorn gov_webui.adapter:app --host 0.0.0.0 --port 8000
