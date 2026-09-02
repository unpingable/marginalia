# SPDX-License-Identifier: Apache-2.0
"""Durable, project-scoped review queue for possible canon.

Generated suggestions are exploratory records. Only an explicit acceptance
action may promote one into the canonical continuity registry.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CanonReviewStoreError(RuntimeError):
    """The canon review queue could not be read or persisted."""


class CanonReviewNotFoundError(CanonReviewStoreError):
    """A requested review candidate does not exist."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CanonReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    kind: str
    confidence: float = 0.0
    subject: str = ""
    statement: str
    field: str = ""
    spans: list[list[int]] = Field(default_factory=list)
    conversation_id: str | None = None
    message_id: str = ""
    status: Literal["pending", "accepted", "dismissed"] = "pending"
    draft: dict[str, Any] | None = None
    promoted_to: str | None = None
    created_at: str
    updated_at: str


class CanonReviewState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    next_sequence: int = 1
    items: dict[str, CanonReviewItem] = Field(default_factory=dict)
    updated_at: str


class CanonReviewStore:
    """Atomic file-backed canon candidate queue for one project."""

    def __init__(self, path: Path, *, project_id: str) -> None:
        self.path = path
        self.project_id = project_id
        self._lock = threading.RLock()
        self._state = self._load_or_create()

    def _fresh(self) -> CanonReviewState:
        return CanonReviewState(updated_at=_now())

    def _load_or_create(self) -> CanonReviewState:
        if not self.path.exists():
            state = self._fresh()
            self._write(state)
            return state
        try:
            return CanonReviewState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            raise CanonReviewStoreError(
                f"cannot load canon review queue at {self.path}: {exc}"
            ) from exc

    def _write(self, state: CanonReviewState) -> None:
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
            raise CanonReviewStoreError(
                f"cannot persist canon review queue at {self.path}: {exc}"
            ) from exc

    def _save(self) -> None:
        self._state.updated_at = _now()
        self._write(self._state)

    def add(
        self,
        *,
        kind: str,
        statement: str,
        confidence: float = 0.0,
        subject: str = "",
        field: str = "",
        spans: list[list[int]] | None = None,
        conversation_id: str | None = None,
        message_id: str = "",
        draft: dict[str, Any] | None = None,
    ) -> CanonReviewItem:
        if not statement.strip():
            raise ValueError("canon review statement must not be empty")
        with self._lock:
            candidate_id = f"cap-{self._state.next_sequence}"
            self._state.next_sequence += 1
            now = _now()
            item = CanonReviewItem(
                id=candidate_id,
                project_id=self.project_id,
                kind=kind,
                confidence=confidence,
                subject=subject,
                statement=statement,
                field=field,
                spans=spans or [],
                conversation_id=conversation_id,
                message_id=message_id,
                draft=draft,
                created_at=now,
                updated_at=now,
            )
            self._state.items[item.id] = item
            self._save()
            return item.model_copy(deep=True)

    def get(self, candidate_id: str) -> CanonReviewItem:
        with self._lock:
            item = self._state.items.get(candidate_id)
            if item is None:
                raise CanonReviewNotFoundError(f"canon review candidate not found: {candidate_id}")
            return item.model_copy(deep=True)

    def list(
        self,
        *,
        status: Literal["pending", "accepted", "dismissed", "all"] = "pending",
    ) -> list[CanonReviewItem]:
        with self._lock:
            items = [
                item.model_copy(deep=True)
                for item in self._state.items.values()
                if status == "all" or item.status == status
            ]
        items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return items

    def resolve(
        self,
        candidate_id: str,
        *,
        status: Literal["accepted", "dismissed"],
        promoted_to: str | None = None,
    ) -> CanonReviewItem:
        with self._lock:
            item = self._state.items.get(candidate_id)
            if item is None:
                raise CanonReviewNotFoundError(f"canon review candidate not found: {candidate_id}")
            if item.status != "pending":
                raise ValueError(f"canon review candidate already {item.status}")
            item.status = status
            item.promoted_to = promoted_to
            item.updated_at = _now()
            self._save()
            return item.model_copy(deep=True)

    def update(
        self,
        candidate_id: str,
        *,
        subject: str | None = None,
        statement: str | None = None,
        kind: str | None = None,
    ) -> CanonReviewItem:
        """Edit a pending suggestion before an explicit canon decision."""
        with self._lock:
            item = self._state.items.get(candidate_id)
            if item is None:
                raise CanonReviewNotFoundError(f"canon review candidate not found: {candidate_id}")
            if item.status != "pending":
                raise ValueError(f"canon review candidate already {item.status}")
            if subject is not None:
                item.subject = " ".join(subject.split())
            if statement is not None:
                cleaned = statement.strip()
                if not cleaned:
                    raise ValueError("canon review statement must not be empty")
                item.statement = cleaned
            if kind is not None:
                cleaned_kind = kind.strip()
                if not cleaned_kind:
                    raise ValueError("canon review kind must not be empty")
                item.kind = cleaned_kind
            item.updated_at = _now()
            self._save()
            return item.model_copy(deep=True)
