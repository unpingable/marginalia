# SPDX-License-Identifier: Apache-2.0
"""Encrypted, expiring custody for exact provider response evidence."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gov_webui.generation_executor import EvidenceReference


KEYRING_SCHEMA = "marginalia.evidence-keyring/v1"
EVIDENCE_SCHEMA = "marginalia.provider-response-evidence/v1"


class EvidenceStoreError(RuntimeError):
    """Evidence is unavailable, invalid, expired, or cannot be persisted."""


@dataclass(frozen=True)
class Keyring:
    active_key_id: str
    keys: dict[str, bytes]

    @classmethod
    def load(cls, path: Path) -> Keyring:
        try:
            stat = path.stat()
            if stat.st_mode & 0o077:
                raise EvidenceStoreError("evidence keyring must not be group/world accessible")
            payload = json.loads(path.read_text(encoding="utf-8"))
        except EvidenceStoreError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceStoreError(f"cannot read evidence keyring: {exc}") from exc
        if not isinstance(payload, dict) or set(payload) != {"schema", "active_key_id", "keys"}:
            raise EvidenceStoreError("evidence keyring does not have the exact v1 shape")
        if payload["schema"] != KEYRING_SCHEMA:
            raise EvidenceStoreError("unsupported evidence keyring schema")
        if not isinstance(payload["keys"], dict) or not payload["keys"]:
            raise EvidenceStoreError("evidence keyring has no keys")
        keys: dict[str, bytes] = {}
        for key_id, encoded in payload["keys"].items():
            if not isinstance(key_id, str) or not key_id or not isinstance(encoded, str):
                raise EvidenceStoreError("invalid evidence key entry")
            try:
                key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            except (ValueError, TypeError) as exc:
                raise EvidenceStoreError("invalid base64 evidence key") from exc
            if len(key) != 32:
                raise EvidenceStoreError("AES-256-GCM evidence keys must be exactly 32 bytes")
            keys[key_id] = key
        active = payload["active_key_id"]
        if active not in keys:
            raise EvidenceStoreError("active evidence key ID is absent")
        return cls(active_key_id=active, keys=keys)


class EncryptedEvidenceStore:
    """Worker-written AES-GCM blobs; app reads are explicit and audited."""

    def __init__(self, root: Path, keyring_path: Path, *, retention_days: int = 30) -> None:
        if not 1 <= retention_days <= 3650:
            raise ValueError("evidence retention_days must be between 1 and 3650")
        self.root = root
        self.keyring_path = keyring_path
        self.retention_days = retention_days
        self.blobs = root / "blobs"
        self.audit_path = root / "access.jsonl"
        self.audit_lock_path = root / ".access.lock"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.blobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.blobs, 0o700)

    def write(
        self,
        response: dict[str, Any],
        *,
        logical_request_id: str,
        dispatch_id: str,
        docket_attempt: str,
        now: datetime | None = None,
    ) -> EvidenceReference:
        created = _utc(now)
        plaintext = _canonical(response)
        response_digest = "sha256:" + hashlib.sha256(plaintext).hexdigest()
        evidence_id = (
            "ev_"
            + hashlib.sha256(
                b"marginalia.evidence-id/v1\0"
                + logical_request_id.encode()
                + b"\0"
                + dispatch_id.encode()
                + b"\0"
                + docket_attempt.encode()
                + b"\0"
                + response_digest.encode()
            ).hexdigest()
        )
        path = self.blobs / f"{evidence_id}.json"
        keyring = Keyring.load(self.keyring_path)
        metadata = {
            "schema": EVIDENCE_SCHEMA,
            "evidence_id": evidence_id,
            "key_id": keyring.active_key_id,
            "logical_request_id": logical_request_id,
            "dispatch_id": dispatch_id,
            "docket_attempt": docket_attempt,
            "response_digest": response_digest,
            "created_at": created.isoformat(),
            "expires_at": (created + timedelta(days=self.retention_days)).isoformat(),
        }
        aad = _canonical(metadata)
        nonce = os.urandom(12)
        ciphertext = AESGCM(keyring.keys[keyring.active_key_id]).encrypt(nonce, plaintext, aad)
        envelope = {
            **metadata,
            "nonce": _b64(nonce),
            "ciphertext": _b64(ciphertext),
        }
        if path.exists():
            existing = self.read(evidence_id, actor="worker-idempotency", now=created)
            if _canonical(existing) != plaintext:
                raise EvidenceStoreError("evidence identity collision")
        else:
            self._write_atomic(path, _canonical(envelope) + b"\n", mode=0o600)
            self._audit("write", evidence_id, "generation-worker", created)
        return EvidenceReference(
            reference=f"evidence:{keyring.active_key_id}:{evidence_id}",
            response_digest=response_digest,
        )

    def read(
        self,
        evidence_id: str,
        *,
        actor: str,
        now: datetime | None = None,
        allow_expired: bool = False,
    ) -> dict[str, Any]:
        if not evidence_id.startswith("ev_") or not evidence_id[3:].isalnum():
            raise EvidenceStoreError("invalid evidence identity")
        path = self.blobs / f"{evidence_id}.json"
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise EvidenceStoreError("evidence body is unavailable or expired") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceStoreError(f"cannot read evidence: {exc}") from exc
        expected = {
            "schema",
            "evidence_id",
            "key_id",
            "logical_request_id",
            "dispatch_id",
            "docket_attempt",
            "response_digest",
            "created_at",
            "expires_at",
            "nonce",
            "ciphertext",
        }
        if not isinstance(envelope, dict) or set(envelope) != expected:
            raise EvidenceStoreError("evidence envelope does not have the exact v1 shape")
        if envelope["schema"] != EVIDENCE_SCHEMA or envelope["evidence_id"] != evidence_id:
            raise EvidenceStoreError("evidence identity or schema mismatch")
        current = _utc(now)
        expires = _parse_time(envelope["expires_at"])
        if current >= expires and not allow_expired:
            self._audit("expired_read_refused", evidence_id, actor, current)
            raise EvidenceStoreError("evidence body has expired")
        keyring = Keyring.load(self.keyring_path)
        key_id = envelope["key_id"]
        if key_id not in keyring.keys:
            raise EvidenceStoreError(f"evidence key version is unavailable: {key_id}")
        metadata = {
            key: value for key, value in envelope.items() if key not in {"nonce", "ciphertext"}
        }
        try:
            plaintext = AESGCM(keyring.keys[key_id]).decrypt(
                _unb64(envelope["nonce"]),
                _unb64(envelope["ciphertext"]),
                _canonical(metadata),
            )
            value = json.loads(plaintext)
        except Exception as exc:
            raise EvidenceStoreError("evidence authentication or decoding failed") from exc
        digest = "sha256:" + hashlib.sha256(plaintext).hexdigest()
        if digest != envelope["response_digest"]:
            raise EvidenceStoreError("evidence response digest mismatch")
        self._audit("read", evidence_id, actor, current)
        return value

    def read_reference(self, reference: str, *, actor: str) -> dict[str, Any]:
        parts = reference.split(":", 2)
        if len(parts) != 3 or parts[0] != "evidence":
            raise EvidenceStoreError("invalid evidence reference")
        key_id, evidence_id = parts[1:]
        value = self.read(evidence_id, actor=actor)
        envelope = json.loads((self.blobs / f"{evidence_id}.json").read_text(encoding="utf-8"))
        if envelope["key_id"] != key_id:
            raise EvidenceStoreError("evidence reference key version mismatch")
        return value

    def purge_expired(self, *, now: datetime | None = None) -> list[str]:
        current = _utc(now)
        purged: list[str] = []
        for path in sorted(self.blobs.glob("ev_*.json")):
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
                if current < _parse_time(envelope["expires_at"]):
                    continue
                evidence_id = envelope["evidence_id"]
                path.unlink()
                self._fsync_directory(self.blobs)
                self._audit("purge", evidence_id, "generation-worker", current)
                purged.append(evidence_id)
            except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
                raise EvidenceStoreError(
                    f"cannot evaluate evidence expiry: {path.name}: {exc}"
                ) from exc
        return purged

    def _audit(self, action: str, evidence_id: str, actor: str, now: datetime) -> None:
        if not actor.strip():
            raise EvidenceStoreError("evidence access requires an actor identity")
        record = (
            _canonical(
                {
                    "schema": "marginalia.evidence-access/v1",
                    "action": action,
                    "actor": actor,
                    "evidence_id": evidence_id,
                    "at": now.isoformat(),
                }
            )
            + b"\n"
        )
        with self.audit_lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            descriptor = os.open(
                self.audit_path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC,
                0o600,
            )
            try:
                os.write(descriptor, record)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @classmethod
    def _write_atomic(cls, path: Path, content: bytes, *, mode: int) -> None:
        descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            cls._fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def create_keyring(path: Path, *, key_id: str, key: bytes | None = None) -> None:
    """Create a new keyring once; callers must arrange separate recovery custody."""
    if not key_id.strip():
        raise ValueError("key_id must not be empty")
    material = key or os.urandom(32)
    if len(material) != 32:
        raise ValueError("evidence key must be 32 bytes")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        raise FileExistsError(path)
    payload = {
        "schema": KEYRING_SCHEMA,
        "active_key_id": key_id,
        "keys": {key_id: _b64(material)},
    }
    EncryptedEvidenceStore._write_atomic(path, _canonical(payload) + b"\n", mode=0o600)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("evidence time must be timezone-aware")
    return result.astimezone(timezone.utc)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("evidence timestamp has no timezone")
    return parsed.astimezone(timezone.utc)
