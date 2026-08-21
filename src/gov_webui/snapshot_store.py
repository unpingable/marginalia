# SPDX-License-Identifier: Apache-2.0
"""Immutable named project snapshots for Marginalia.

Snapshots contain the same portable JSON payload returned by the project
export API. They live outside a project's governor context so a checkpoint can
still be read if that context needs manual recovery.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SnapshotStoreError(RuntimeError):
    """Snapshot metadata or content could not be read or persisted."""


class SnapshotNotFoundError(SnapshotStoreError):
    """A requested snapshot does not exist."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    name: str
    created_at: str
    content_hash: str
    counts: dict[str, int] = Field(default_factory=dict)


class SnapshotIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    snapshots: dict[str, ProjectSnapshot] = Field(default_factory=dict)
    updated_at: str


class ProjectSnapshotStore:
    """Crash-safe immutable JSON snapshots for one project."""

    MAX_NAME_LENGTH = 160

    def __init__(self, root: Path, *, project_id: str) -> None:
        self.root = root / project_id
        self.project_id = project_id
        self.index_path = self.root / "index.json"
        self.content_dir = self.root / "content"
        self._lock = threading.RLock()
        self._index = self._load_or_create()

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("snapshot name must not be empty")
        if len(cleaned) > ProjectSnapshotStore.MAX_NAME_LENGTH:
            raise ValueError(
                f"snapshot name is limited to {ProjectSnapshotStore.MAX_NAME_LENGTH} characters"
            )
        return cleaned

    def _fresh(self) -> SnapshotIndex:
        return SnapshotIndex(updated_at=_now())

    def _load_or_create(self) -> SnapshotIndex:
        if not self.index_path.exists():
            index = self._fresh()
            self._write_index(index)
            return index
        try:
            return SnapshotIndex.model_validate_json(
                self.index_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise SnapshotStoreError(
                f"cannot load snapshot index at {self.index_path}: {exc}"
            ) from exc

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    def _write_index(self, index: SnapshotIndex) -> None:
        try:
            self._write_atomic(self.index_path, index.model_dump_json(indent=2) + "\n")
        except OSError as exc:
            raise SnapshotStoreError(f"cannot persist snapshot index: {exc}") from exc

    def create(self, *, name: str, payload: dict[str, Any]) -> ProjectSnapshot:
        """Write content before publishing immutable metadata in the index."""
        cleaned = self._clean_name(name)
        encoded = json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        snapshot = ProjectSnapshot(
            id=uuid4().hex[:12],
            project_id=self.project_id,
            name=cleaned,
            created_at=_now(),
            content_hash=digest,
            counts={
                "conversations": len(payload.get("conversations", [])),
                "artifacts": len(payload.get("artifacts", [])),
                "manuscript_nodes": len(payload.get("manuscript", {}).get("nodes", [])),
            },
        )
        with self._lock:
            try:
                self._write_atomic(self.content_dir / f"{snapshot.id}.json", encoded)
            except OSError as exc:
                raise SnapshotStoreError(f"cannot persist snapshot content: {exc}") from exc
            self._index.snapshots[snapshot.id] = snapshot
            self._index.updated_at = _now()
            self._write_index(self._index)
        return snapshot.model_copy(deep=True)

    def list(self) -> list[ProjectSnapshot]:
        with self._lock:
            snapshots = [item.model_copy(deep=True) for item in self._index.snapshots.values()]
        snapshots.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return snapshots

    def get(self, snapshot_id: str) -> tuple[ProjectSnapshot, dict[str, Any]]:
        with self._lock:
            snapshot = self._index.snapshots.get(snapshot_id)
            if snapshot is None:
                raise SnapshotNotFoundError(f"snapshot not found: {snapshot_id}")
            path = self.content_dir / f"{snapshot.id}.json"
            try:
                encoded = path.read_text(encoding="utf-8")
                payload = json.loads(encoded)
            except (OSError, json.JSONDecodeError) as exc:
                raise SnapshotStoreError(
                    f"cannot read snapshot content at {path}: {exc}"
                ) from exc
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if digest != snapshot.content_hash:
                raise SnapshotStoreError(
                    f"snapshot integrity check failed: {snapshot.id}"
                )
            return snapshot.model_copy(deep=True), payload
