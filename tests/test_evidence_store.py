# SPDX-License-Identifier: Apache-2.0
"""Encrypted provider-evidence custody and recovery tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gov_webui.evidence_store import (
    EncryptedEvidenceStore,
    EvidenceStoreError,
    create_keyring,
)


NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


def fixture(tmp_path: Path):
    keyring = tmp_path / "separate-recovery" / "keys.json"
    create_keyring(keyring, key_id="key-2026-09", key=b"k" * 32)
    return EncryptedEvidenceStore(tmp_path / "live-evidence", keyring, retention_days=2), keyring


def test_exact_response_is_encrypted_and_read_is_audited(tmp_path: Path) -> None:
    store, _keyring = fixture(tmp_path)
    response = {"content": "private manuscript", "usage": {"total_tokens": 3}}
    reference = store.write(
        response,
        logical_request_id="gen-1",
        dispatch_id="dispatch-1",
        docket_attempt="attempt-1",
        now=NOW,
    )

    evidence_id = reference.reference.rsplit(":", 1)[1]
    raw = (store.blobs / f"{evidence_id}.json").read_text(encoding="utf-8")
    assert "private manuscript" not in raw
    assert store.read(evidence_id, actor="acceptance-service", now=NOW) == response
    audit = [json.loads(line) for line in store.audit_path.read_text().splitlines()]
    assert [item["action"] for item in audit] == ["write", "read"]
    assert all("content" not in item for item in audit)


def test_metadata_or_ciphertext_tampering_refuses(tmp_path: Path) -> None:
    store, _keyring = fixture(tmp_path)
    reference = store.write(
        {"content": "result"},
        logical_request_id="gen-1",
        dispatch_id="dispatch-1",
        docket_attempt="attempt-1",
        now=NOW,
    )
    evidence_id = reference.reference.rsplit(":", 1)[1]
    path = store.blobs / f"{evidence_id}.json"
    envelope = json.loads(path.read_text())
    envelope["dispatch_id"] = "substituted"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(EvidenceStoreError, match="authentication"):
        store.read(evidence_id, actor="acceptance-service", now=NOW)


def test_expiry_removes_live_body_but_retained_backup_ciphertext_remains(tmp_path: Path) -> None:
    store, _keyring = fixture(tmp_path)
    reference = store.write(
        {"content": "result"},
        logical_request_id="gen-1",
        dispatch_id="dispatch-1",
        docket_attempt="attempt-1",
        now=NOW,
    )
    evidence_id = reference.reference.rsplit(":", 1)[1]
    backup_ciphertext = (store.blobs / f"{evidence_id}.json").read_bytes()

    assert store.purge_expired(now=NOW + timedelta(days=3)) == [evidence_id]
    with pytest.raises(EvidenceStoreError, match="unavailable or expired"):
        store.read(evidence_id, actor="acceptance-service", now=NOW + timedelta(days=3))
    assert b"result" not in backup_ciphertext
    assert backup_ciphertext  # retained archives are governed by backup retention, not live expiry


def test_restore_requires_the_separately_supplied_matching_key(tmp_path: Path) -> None:
    store, keyring = fixture(tmp_path)
    reference = store.write(
        {"content": "restorable"},
        logical_request_id="gen-1",
        dispatch_id="dispatch-1",
        docket_attempt="attempt-1",
        now=NOW,
    )
    evidence_id = reference.reference.rsplit(":", 1)[1]
    restored = tmp_path / "restored"
    restored.mkdir()
    restored_blobs = restored / "blobs"
    restored_blobs.mkdir()
    (restored_blobs / f"{evidence_id}.json").write_bytes(
        (store.blobs / f"{evidence_id}.json").read_bytes()
    )

    wrong_keyring = tmp_path / "wrong.json"
    create_keyring(wrong_keyring, key_id="key-2026-09", key=b"x" * 32)
    with pytest.raises(EvidenceStoreError, match="authentication"):
        EncryptedEvidenceStore(restored, wrong_keyring).read(
            evidence_id, actor="restore-test", now=NOW
        )

    assert (
        EncryptedEvidenceStore(restored, keyring).read(evidence_id, actor="restore-test", now=NOW)[
            "content"
        ]
        == "restorable"
    )


def test_keyring_permissions_are_enforced(tmp_path: Path) -> None:
    store, keyring = fixture(tmp_path)
    keyring.chmod(0o644)
    with pytest.raises(EvidenceStoreError, match="group/world"):
        store.write(
            {"content": "no"},
            logical_request_id="gen-1",
            dispatch_id="dispatch-1",
            docket_attempt="attempt-1",
            now=NOW,
        )
