# SPDX-License-Identifier: Apache-2.0
"""Deployment secret bootstrap behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from gov_webui.evidence_store import Keyring
from gov_webui.generation_secrets import create_generation_secrets
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
