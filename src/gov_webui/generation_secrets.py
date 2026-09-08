# SPDX-License-Identifier: Apache-2.0
"""Create separately recoverable secrets for durable generation."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gov_webui.evidence_store import create_keyring


_RING_PKCS8_PREFIX = bytes.fromhex("3051020101300506032b657004220420")
_RING_PUBLIC_PREFIX = bytes.fromhex("812100")


def _create_ring_identity(path: Path) -> bytes:
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
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        os.write(descriptor, _RING_PKCS8_PREFIX + seed + _RING_PUBLIC_PREFIX + public)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return public


def _principal(role: str, public: bytes) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            b"marginalia.ag-ng.provider-rpc-principal/v1\0" + role.encode("ascii") + b"\0" + public
        ).hexdigest()
    )


def create_generation_secrets(directory: Path, *, key_id: str) -> tuple[Path, Path]:
    """Create both files once, outside the data/backup root."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    issuer = directory / "marginalia-ag-issuer.pk8"
    evidence = directory / "marginalia-evidence-keys.json"
    if issuer.exists() or evidence.exists():
        raise FileExistsError("generation secret files already exist; refusing partial replacement")

    _create_ring_identity(issuer)
    try:
        create_keyring(evidence, key_id=key_id)
    except Exception:
        issuer.unlink(missing_ok=True)
        raise
    return issuer, evidence


def create_provider_rpc_identities(directory: Path) -> tuple[Path, Path, Path]:
    """Create isolated providerd/providerctl keys plus non-secret public metadata."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    providerd_dir = directory / "providerd"
    providerctl_dir = directory / "providerctl"
    metadata = directory / "provider-identities.json"
    targets = (providerd_dir / "rpc.pk8", providerctl_dir / "rpc.pk8", metadata)
    if any(path.exists() for path in targets):
        raise FileExistsError(
            "provider RPC identity files already exist; refusing partial replacement"
        )
    for child in (providerd_dir, providerctl_dir):
        child.mkdir(mode=0o700)
    created: list[Path] = []
    try:
        providerd_public = _create_ring_identity(targets[0])
        created.append(targets[0])
        providerctl_public = _create_ring_identity(targets[1])
        created.append(targets[1])
        document = {
            "schema": "marginalia.ag-ng-provider-identities/v1",
            "providerctl": {
                "key_id": "marginalia-providerctl.v1",
                "principal": _principal("providerctl", providerctl_public),
                "public_key": base64.urlsafe_b64encode(providerctl_public).rstrip(b"=").decode(),
            },
            "providerd": {
                "key_id": "marginalia-providerd.v1",
                "principal": _principal("providerd", providerd_public),
                "public_key": base64.urlsafe_b64encode(providerd_public).rstrip(b"=").decode(),
            },
        }
        descriptor = os.open(metadata, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        try:
            os.write(
                descriptor,
                json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n",
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        created.append(metadata)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--key-id", default="marginalia-evidence-v1")
    parser.add_argument("--provider-identities-only", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.provider_identities_only:
            providerd, providerctl, metadata = create_provider_rpc_identities(arguments.directory)
            created = (providerd, providerctl, metadata)
        else:
            expected = (
                arguments.directory / "marginalia-ag-issuer.pk8",
                arguments.directory / "marginalia-evidence-keys.json",
                arguments.directory / "providerd" / "rpc.pk8",
                arguments.directory / "providerctl" / "rpc.pk8",
                arguments.directory / "provider-identities.json",
            )
            if any(path.exists() for path in expected):
                raise FileExistsError(
                    "deployment secret files already exist; refusing partial replacement"
                )
            issuer, evidence = create_generation_secrets(
                arguments.directory, key_id=arguments.key_id
            )
            try:
                providerd, providerctl, metadata = create_provider_rpc_identities(
                    arguments.directory
                )
            except Exception:
                issuer.unlink(missing_ok=True)
                evidence.unlink(missing_ok=True)
                raise
            created = (issuer, evidence, providerd, providerctl, metadata)
    except Exception as exc:
        sys.stderr.write(f"generation secret creation refused: {exc}\n")
        return 1
    for path in created:
        print(f"created {path}")
    print("Keep a separate recoverable copy; data-volume backups intentionally omit these files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
