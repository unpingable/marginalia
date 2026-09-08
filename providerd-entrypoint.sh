#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT=/var/lib/agent-governor/providerd
SOCKET_ROOT=/run/marginalia/providerd
INPUT_ROOT="${MARGINALIA_INPUT_ROOT:-/run/marginalia-input}"
CONFIG_ROOT=/etc/marginalia
CREDENTIAL_ROOT=/run/credentials/marginalia-providerd

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

mkdir -p "$STATE_ROOT/objects" "$SOCKET_ROOT" "$CONFIG_ROOT" "$CREDENTIAL_ROOT"
chmod 0700 "$STATE_ROOT" "$STATE_ROOT/objects"
chmod 02750 "$SOCKET_ROOT"
chmod 0700 "$CONFIG_ROOT" "$CREDENTIAL_ROOT"

install_private_file "$INPUT_ROOT/config/providerd.toml" "$CONFIG_ROOT/providerd.toml" "provider policy"
found_rpc=false
for source_path in "$INPUT_ROOT/providerd-secrets"/*; do
    [ -e "$source_path" ] || continue
    name="${source_path##*/}"
    install_private_file "$source_path" "$CREDENTIAL_ROOT/$name" "provider credential"
    [ "$name" = rpc.pk8 ] && found_rpc=true
done
if [ "$found_rpc" != true ]; then
    echo "Refusing provider startup without providerd RPC identity" >&2
    exit 1
fi
export CREDENTIALS_DIRECTORY="$CREDENTIAL_ROOT"

exec /usr/local/bin/ag-providerd --config "$CONFIG_ROOT/providerd.toml"
