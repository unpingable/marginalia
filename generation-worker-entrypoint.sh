#!/usr/bin/env bash
set -euo pipefail

INPUT_ROOT="${MARGINALIA_INPUT_ROOT:-/run/marginalia-input}"
CONFIG_ROOT=/etc/marginalia
SECRET_ROOT=/run/secrets/marginalia-generation
CREDENTIAL_ROOT=/run/credentials/marginalia-generation-worker

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

mkdir -p "$CONFIG_ROOT" "$SECRET_ROOT" "$CREDENTIAL_ROOT"
chmod 0700 "$CONFIG_ROOT" "$SECRET_ROOT" "$CREDENTIAL_ROOT"
install_private_file "$INPUT_ROOT/config/providerd.toml" "$CONFIG_ROOT/providerd.toml" "provider policy"
install_private_file "$INPUT_ROOT/config/providerctl.toml" "$CONFIG_ROOT/providerctl.toml" "provider client policy"
install_private_file "$INPUT_ROOT/config/providers.json" "$CONFIG_ROOT/providers.json" "model catalog"
install_private_file "$INPUT_ROOT/worker-secrets/marginalia-ag-issuer.pk8" "$SECRET_ROOT/marginalia-ag-issuer.pk8" "authorization issuer key"
install_private_file "$INPUT_ROOT/worker-secrets/marginalia-evidence-keys.json" "$SECRET_ROOT/marginalia-evidence-keys.json" "evidence keyring"
install_private_file "$INPUT_ROOT/worker-secrets/rpc.pk8" "$CREDENTIAL_ROOT/rpc.pk8" "provider client RPC identity"

export MARGINALIA_AG_ISSUER_KEY_FILE="$SECRET_ROOT/marginalia-ag-issuer.pk8"
export MARGINALIA_EVIDENCE_KEY_FILE="$SECRET_ROOT/marginalia-evidence-keys.json"
export MARGINALIA_AG_PROVIDERCTL_CONFIG="$CONFIG_ROOT/providerctl.toml"
export MARGINALIA_MODEL_CONFIG="$CONFIG_ROOT/providers.json"

exec marginalia-generation-worker
