# SPDX-License-Identifier: Apache-2.0
"""Deployment secret bootstrap behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from gov_webui.evidence_store import Keyring
from gov_webui.generation_secrets import create_generation_secrets, create_provider_rpc_identities
from gov_webui.generation_worker import ring_ed25519_public_key


def test_generation_secrets_are_separate_strict_and_never_replaced(tmp_path: Path) -> None:
    directory = tmp_path / "separate-recovery"
    issuer, evidence = create_generation_secrets(directory, key_id="evidence-2026-09")

    assert len(ring_ed25519_public_key(issuer.read_bytes())) == 32
    assert Keyring.load(evidence).active_key_id == "evidence-2026-09"
    assert directory.stat().st_mode & 0o077 == 0
    assert issuer.stat().st_mode & 0o077 == 0
    assert evidence.stat().st_mode & 0o077 == 0

    issuer_before = issuer.read_bytes()
    evidence_before = evidence.read_bytes()
    with pytest.raises(FileExistsError, match="refusing partial replacement"):
        create_generation_secrets(directory, key_id="replacement")
    assert issuer.read_bytes() == issuer_before
    assert evidence.read_bytes() == evidence_before


def test_provider_rpc_identities_are_isolated_and_public_metadata_is_recoverable(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "recovery"
    providerd, providerctl, metadata = create_provider_rpc_identities(directory)

    assert len(ring_ed25519_public_key(providerd.read_bytes())) == 32
    assert len(ring_ed25519_public_key(providerctl.read_bytes())) == 32
    document = __import__("json").loads(metadata.read_text(encoding="utf-8"))
    assert document["schema"] == "marginalia.ag-ng-provider-identities/v1"
    assert document["providerd"]["principal"].startswith("sha256:")
    assert document["providerctl"]["principal"].startswith("sha256:")
    assert providerd.parent.stat().st_mode & 0o077 == 0
    assert providerctl.parent.stat().st_mode & 0o077 == 0
    assert all(path.stat().st_mode & 0o077 == 0 for path in (providerd, providerctl, metadata))

    with pytest.raises(FileExistsError, match="partial replacement"):
        create_provider_rpc_identities(directory)


def test_default_cli_preflight_does_not_create_half_a_secret_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gov_webui import generation_secrets

    directory = tmp_path / "existing"
    (directory / "providerd").mkdir(parents=True)
    (directory / "providerd" / "rpc.pk8").write_bytes(b"preserved")
    monkeypatch.setattr("sys.argv", ["marginalia-generation-secrets", str(directory)])

    assert generation_secrets.main() == 1
    assert not (directory / "marginalia-ag-issuer.pk8").exists()
    assert not (directory / "marginalia-evidence-keys.json").exists()
