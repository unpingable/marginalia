# SPDX-License-Identifier: Apache-2.0
"""Ordered manuscript structure referencing versioned Marginalia artifacts."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ManuscriptStoreError(RuntimeError):
    """The manuscript structure could not be read or persisted."""


class ManuscriptNodeNotFoundError(ManuscriptStoreError):
    """A requested manuscript node does not exist."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ManuscriptNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["part", "chapter", "scene"]
    title: str
    parent_id: str | None = None
    artifact_id: str | None = None
    status: Literal["idea", "drafting", "revised", "final"] = "idea"
    position: int = 0
    created_at: str
    updated_at: str


class ManuscriptState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    version: int = 1
    nodes: dict[str, ManuscriptNode] = Field(default_factory=dict)
    updated_at: str


class ManuscriptStore:
    """Crash-safe flat node store with explicit hierarchy and sibling order."""

    MAX_TITLE_LENGTH = 240
    ALLOWED_KINDS = {"part", "chapter", "scene"}
    ALLOWED_STATUSES = {"idea", "drafting", "revised", "final"}

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._state = self._load_or_create()

    @staticmethod
    def _clean_title(title: str) -> str:
        cleaned = " ".join(title.split())
        if not cleaned:
            raise ValueError("manuscript title must not be empty")
        if len(cleaned) > ManuscriptStore.MAX_TITLE_LENGTH:
            raise ValueError(
                f"manuscript title is limited to {ManuscriptStore.MAX_TITLE_LENGTH} characters"
            )
        return cleaned

    @classmethod
    def _validate_kind(cls, kind: str) -> None:
        if kind not in cls.ALLOWED_KINDS:
            raise ValueError(f"invalid manuscript kind: {kind}")

    @classmethod
    def _validate_status(cls, status: str) -> None:
        if status not in cls.ALLOWED_STATUSES:
            raise ValueError(f"invalid manuscript status: {status}")

    def _fresh(self) -> ManuscriptState:
        return ManuscriptState(updated_at=_now())

    def _load_or_create(self) -> ManuscriptState:
        if not self.path.exists():
            state = self._fresh()
            self._write(state)
            return state
        try:
            return ManuscriptState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            raise ManuscriptStoreError(f"cannot load manuscript at {self.path}: {exc}") from exc

    def _write(self, state: ManuscriptState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(state.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ManuscriptStoreError(f"cannot persist manuscript at {self.path}: {exc}") from exc

    def _save(self) -> None:
        self._state.version += 1
        self._state.updated_at = _now()
        self._write(self._state)

    def _require_parent(self, parent_id: str | None) -> None:
        if parent_id is not None and parent_id not in self._state.nodes:
            raise ManuscriptNodeNotFoundError(f"manuscript parent not found: {parent_id}")

    def _siblings(self, parent_id: str | None) -> list[ManuscriptNode]:
        siblings = [node for node in self._state.nodes.values() if node.parent_id == parent_id]
        siblings.sort(key=lambda node: (node.position, node.created_at, node.id))
        return siblings

    def _renumber(self, parent_id: str | None, ordered_ids: list[str]) -> None:
        now = _now()
        for position, node_id in enumerate(ordered_ids):
            node = self._state.nodes[node_id]
            node.parent_id = parent_id
            node.position = position
            node.updated_at = now

    def list_nodes(self) -> tuple[list[ManuscriptNode], int]:
        with self._lock:
            nodes = [node.model_copy(deep=True) for node in self._state.nodes.values()]
            version = self._state.version
        nodes.sort(
            key=lambda node: (
                node.parent_id or "",
                node.position,
                node.created_at,
                node.id,
            )
        )
        return nodes, version

    def ordered_depth_first(self) -> tuple[list[ManuscriptNode], int]:
        """Return manuscript nodes in reader order, parents before children."""
        with self._lock:
            version = self._state.version
            children: dict[str | None, list[ManuscriptNode]] = {}
            for node in self._state.nodes.values():
                children.setdefault(node.parent_id, []).append(node)
            for siblings in children.values():
                siblings.sort(key=lambda item: (item.position, item.created_at, item.id))
            ordered: list[ManuscriptNode] = []

            def visit(parent_id: str | None) -> None:
                for child in children.get(parent_id, []):
                    ordered.append(child.model_copy(deep=True))
                    visit(child.id)

            visit(None)
        return ordered, version

    def get(self, node_id: str) -> ManuscriptNode:
        with self._lock:
            node = self._state.nodes.get(node_id)
            if node is None:
                raise ManuscriptNodeNotFoundError(f"manuscript node not found: {node_id}")
            return node.model_copy(deep=True)

    def create(
        self,
        *,
        kind: Literal["part", "chapter", "scene"],
        title: str,
        parent_id: str | None = None,
        artifact_id: str | None = None,
        status: Literal["idea", "drafting", "revised", "final"] = "idea",
    ) -> ManuscriptNode:
        with self._lock:
            self._validate_kind(kind)
            self._validate_status(status)
            self._require_parent(parent_id)
            now = _now()
            node = ManuscriptNode(
                id=uuid4().hex[:12],
                kind=kind,
                title=self._clean_title(title),
                parent_id=parent_id,
                artifact_id=artifact_id,
                status=status,
                position=len(self._siblings(parent_id)),
                created_at=now,
                updated_at=now,
            )
            self._state.nodes[node.id] = node
            self._save()
            return node.model_copy(deep=True)

    def update(
        self,
        node_id: str,
        *,
        title: str | None = None,
        artifact_id: str | None = None,
        set_artifact: bool = False,
        status: Literal["idea", "drafting", "revised", "final"] | None = None,
    ) -> ManuscriptNode:
        with self._lock:
            node = self._state.nodes.get(node_id)
            if node is None:
                raise ManuscriptNodeNotFoundError(f"manuscript node not found: {node_id}")
            if title is not None:
                node.title = self._clean_title(title)
            if set_artifact:
                node.artifact_id = artifact_id
            if status is not None:
                self._validate_status(status)
                node.status = status
            node.updated_at = _now()
            self._save()
            return node.model_copy(deep=True)

    def move(
        self,
        node_id: str,
        *,
        parent_id: str | None,
        position: int,
    ) -> ManuscriptNode:
        with self._lock:
            node = self._state.nodes.get(node_id)
            if node is None:
                raise ManuscriptNodeNotFoundError(f"manuscript node not found: {node_id}")
            self._require_parent(parent_id)
            if parent_id == node_id:
                raise ValueError("a manuscript node cannot contain itself")
            ancestor = parent_id
            while ancestor is not None:
                if ancestor == node_id:
                    raise ValueError("a manuscript node cannot move inside its descendant")
                ancestor = self._state.nodes[ancestor].parent_id

            old_parent = node.parent_id
            old_ids = [item.id for item in self._siblings(old_parent) if item.id != node_id]
            self._renumber(old_parent, old_ids)
            target_ids = [item.id for item in self._siblings(parent_id) if item.id != node_id]
            insertion = max(0, min(position, len(target_ids)))
            target_ids.insert(insertion, node_id)
            self._renumber(parent_id, target_ids)
            self._save()
            return self._state.nodes[node_id].model_copy(deep=True)

    def delete(self, node_id: str) -> bool:
        with self._lock:
            if node_id not in self._state.nodes:
                return False
            if any(node.parent_id == node_id for node in self._state.nodes.values()):
                raise ValueError("move or delete child nodes first")
            parent_id = self._state.nodes[node_id].parent_id
            del self._state.nodes[node_id]
            self._renumber(parent_id, [item.id for item in self._siblings(parent_id)])
            self._save()
            return True
