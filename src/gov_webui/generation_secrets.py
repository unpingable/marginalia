# SPDX-License-Identifier: Apache-2.0
"""Create separately recoverable secrets for durable generation."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gov_webui.evidence_store import create_keyring


_RING_PKCS8_PREFIX = bytes.fromhex("3051020101300506032b657004220420")
_RING_PUBLIC_PREFIX = bytes.fromhex("812100")


def create_generation_secrets(directory: Path, *, key_id: str) -> tuple[Path, Path]:
    """Create both files once, outside the data/backup root."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    issuer = directory / "marginalia-ag-issuer.pk8"
    evidence = directory / "marginalia-evidence-keys.json"
    if issuer.exists() or evidence.exists():
        raise FileExistsError("generation secret files already exist; refusing partial replacement")

    private = Ed25519PrivateKey.generate()
    seed = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    descriptor = os.open(issuer, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, _RING_PKCS8_PREFIX + seed + _RING_PUBLIC_PREFIX + public)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        create_keyring(evidence, key_id=key_id)
    except Exception:
        issuer.unlink(missing_ok=True)
        raise
    return issuer, evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--key-id", default="marginalia-evidence-v1")
    arguments = parser.parse_args()
    try:
        issuer, evidence = create_generation_secrets(arguments.directory, key_id=arguments.key_id)
    except Exception as exc:
        sys.stderr.write(f"generation secret creation refused: {exc}\n")
        return 1
    print(f"created {issuer}")
    print(f"created {evidence}")
    print("Keep a separate recoverable copy; data-volume backups intentionally omit these files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
