#!/usr/bin/env sh
# SPDX-License-Identifier: Apache-2.0
set -eu

VERSION="0.1.0"
INSTALL_DIR="${MARGINALIA_INSTALL_DIR:-$HOME/.local/bin}"
SOURCE="${MARGINALIA_INSTALL_SOURCE:-https://github.com/unpingable/marginalia/releases/download/v${VERSION}/marginalia}"
CHECKSUM_SOURCE="${MARGINALIA_INSTALL_CHECKSUM_SOURCE:-https://github.com/unpingable/marginalia/releases/download/v${VERSION}/marginalia.sha256}"
DESTINATION="$INSTALL_DIR/marginalia"

mkdir -p "$INSTALL_DIR"
TEMP_FILE="$(mktemp "${TMPDIR:-/tmp}/marginalia-install.XXXXXX")"
TEMP_CHECKSUM="$(mktemp "${TMPDIR:-/tmp}/marginalia-checksum.XXXXXX")"
trap 'rm -f "$TEMP_FILE" "$TEMP_CHECKSUM"' EXIT HUP INT TERM

download() {
  source_path="$1"
  destination_path="$2"
  if [ -f "$source_path" ]; then
    cp "$source_path" "$destination_path"
  elif command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error "$source_path" --output "$destination_path"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$source_path" -O "$destination_path"
  else
    echo "Marginalia: curl or wget is required to download the launcher" >&2
    exit 1
  fi
}

download "$SOURCE" "$TEMP_FILE"

EXPECTED_SHA256="${MARGINALIA_INSTALL_SHA256:-}"
if [ -z "$EXPECTED_SHA256" ] && [ ! -f "$SOURCE" ]; then
  download "$CHECKSUM_SOURCE" "$TEMP_CHECKSUM"
  EXPECTED_SHA256="$(awk 'NR == 1 {print $1}' "$TEMP_CHECKSUM")"
fi
if [ -n "$EXPECTED_SHA256" ]; then
  if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA256="$(sha256sum "$TEMP_FILE" | awk '{print $1}')"
  elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_SHA256="$(shasum -a 256 "$TEMP_FILE" | awk '{print $1}')"
  else
    echo "Marginalia: sha256sum or shasum is required to verify the launcher" >&2
    exit 1
  fi
  if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
    echo "Marginalia: downloaded launcher failed checksum verification" >&2
    exit 1
  fi
else
  echo "Marginalia: local development launcher; release checksum was not requested" >&2
fi

install -m 0755 "$TEMP_FILE" "$DESTINATION"
rm -f "$TEMP_FILE" "$TEMP_CHECKSUM"
trap - 0 HUP INT TERM
echo "Installed Marginalia at $DESTINATION"

case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *) printf 'Add %s to PATH when you want to run marginalia directly.\n' "$INSTALL_DIR" ;;
esac

if [ "${MARGINALIA_INSTALL_ONLY:-0}" != "1" ]; then
  exec "$DESTINATION" start "$@"
fi
