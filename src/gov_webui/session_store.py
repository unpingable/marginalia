# SPDX-License-Identifier: Apache-2.0
"""Marginalia-owned conversation persistence with additive model provenance."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterator

import fcntl


class SessionWriteResult(StrEnum):
    """Terminal result of a revision-checked durable session write."""

    COMMITTED = "committed"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"


@dataclass
class SessionMessage:
    """One persisted conversation message."""

    id: str
    role: str
    content: str
    timestamp: str
    model: str | None = None
    usage: dict[str, int] | None = None
    provider_id: str | None = None
    model_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }
        if self.model is not None:
            result["model"] = self.model
        if self.usage is not None:
            result["usage"] = self.usage
        if self.provider_id is not None:
            result["provider_id"] = self.provider_id
        if self.model_id is not None:
            result["model_id"] = self.model_id
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMessage:
        return cls(
            id=data["id"],
            role=data["role"],
            content=data["content"],
            timestamp=data["timestamp"],
            model=data.get("model"),
            usage=data.get("usage"),
            provider_id=data.get("provider_id"),
            model_id=data.get("model_id"),
        )

    @classmethod
    def create(
        cls,
        role: str,
        content: str,
        model: str | None = None,
        usage: dict[str, int] | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> SessionMessage:
        return cls(
            id=uuid.uuid4().hex[:12],
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
            model=model,
            usage=usage,
            provider_id=provider_id,
            model_id=model_id,
        )


@dataclass
class ChatSession:
    """A persistent conversation; model is the selection for future turns."""

    id: str
    context_id: str
    title: str
    created_at: str
    updated_at: str
    model: str
    revision: int = 0
    message_count: int = 0
    messages: list[SessionMessage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "context_id": self.context_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "revision": self.revision,
            "message_count": self.message_count,
            "messages": [message.to_dict() for message in self.messages],
        }

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "context_id": self.context_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "revision": self.revision,
            "message_count": self.message_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatSession:
        return cls(
            id=data["id"],
            context_id=data["context_id"],
            title=data["title"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            model=data.get("model", ""),
            revision=data.get("revision", 0),
            message_count=data.get("message_count", 0),
            messages=[SessionMessage.from_dict(message) for message in data.get("messages", [])],
        )


class SessionStore:
    """File-per-conversation JSON persistence compatible with legacy records."""

    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir

    def _ensure_dir(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        safe_id = re.sub(r"[^\w\-.]", "_", session_id)
        return self.sessions_dir / f"{safe_id}.json"

    def _lock_path(self, session_id: str) -> Path:
        safe_id = re.sub(r"[^\w\-.]", "_", session_id)
        return self.sessions_dir / f".{safe_id}.lock"

    @contextmanager
    def _session_lock(self, session_id: str) -> Iterator[None]:
        """Serialize one session's read-modify-write cycle across processes."""
        self._ensure_dir()
        with self._lock_path(session_id).open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _write_session(self, session: ChatSession) -> None:
        """Atomically replace a session file; callers serialize mutations."""
        self._ensure_dir()
        target = self._session_path(session.id)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.sessions_dir,
            prefix=f".{target.stem}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                temporary.write(json.dumps(session.to_dict(), indent=2) + "\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    def _read_session(self, session_id: str) -> ChatSession | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            return ChatSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def create(
        self,
        context_id: str,
        model: str = "",
        title: str = "New conversation",
    ) -> ChatSession:
        now = datetime.now(timezone.utc).isoformat()
        session = ChatSession(
            id=uuid.uuid4().hex[:16],
            context_id=context_id,
            title=title,
            created_at=now,
            updated_at=now,
            model=model,
        )
        self._write_session(session)
        return session

    def get(self, session_id: str) -> ChatSession | None:
        return self._read_session(session_id)

    def list_summaries(self) -> list[dict[str, Any]]:
        if not self.sessions_dir.exists():
            return []
        summaries: list[dict[str, Any]] = []
        for path in self.sessions_dir.glob("*.json"):
            try:
                session = ChatSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
                summaries.append(session.to_summary())
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        summaries.sort(key=lambda item: item["updated_at"], reverse=True)
        return summaries

    def delete(self, session_id: str) -> bool:
        with self._session_lock(session_id):
            path = self._session_path(session_id)
            if not path.exists():
                return False
            path.unlink()
            return True

    def update_title(self, session_id: str, title: str) -> bool:
        with self._session_lock(session_id):
            session = self._read_session(session_id)
            if session is None:
                return False
            session.title = title
            session.revision += 1
            session.updated_at = datetime.now(timezone.utc).isoformat()
            self._write_session(session)
            return True

    def update_model(self, session_id: str, model: str) -> bool:
        with self._session_lock(session_id):
            session = self._read_session(session_id)
            if session is None:
                return False
            session.model = model
            session.revision += 1
            session.updated_at = datetime.now(timezone.utc).isoformat()
            self._write_session(session)
            return True

    def append_message(self, session_id: str, msg: SessionMessage) -> bool:
        return self.append_messages(session_id, [msg])

    def append_messages(self, session_id: str, messages: list[SessionMessage]) -> bool:
        """Commit one successful turn with a single session-file write."""
        with self._session_lock(session_id):
            session = self._read_session(session_id)
            if session is None:
                return False
            self._append_to_session(session, messages)
            return True

    def append_messages_if_revision(
        self,
        session_id: str,
        expected_revision: int,
        messages: list[SessionMessage],
    ) -> SessionWriteResult:
        """Append only when durable state still matches the generation snapshot."""
        with self._session_lock(session_id):
            session = self._read_session(session_id)
            if session is None:
                return SessionWriteResult.NOT_FOUND
            if session.revision != expected_revision:
                return SessionWriteResult.CONFLICT
            self._append_to_session(session, messages)
            return SessionWriteResult.COMMITTED

    def move_to(
        self,
        session_id: str,
        target_store: SessionStore,
        context_id: str,
        commit_metadata: Callable[[], None],
    ) -> ChatSession | None:
        """Move content while preventing an in-flight writer from using the old path."""
        if self.sessions_dir.resolve() == target_store.sessions_dir.resolve():
            raise ValueError("source and target session stores must differ")
        with self._session_lock(session_id):
            session = self._read_session(session_id)
            if session is None:
                return None
            with target_store._session_lock(session_id):
                if target_store._read_session(session_id) is not None:
                    raise FileExistsError(f"target session already exists: {session_id}")
                session.context_id = context_id
                session.revision += 1
                target_store._write_session(session)
                try:
                    commit_metadata()
                except Exception:
                    target_store._session_path(session_id).unlink(missing_ok=True)
                    raise
                self._session_path(session_id).unlink()
            return session

    def _append_to_session(self, session: ChatSession, messages: list[SessionMessage]) -> None:
        session.messages.extend(messages)
        session.message_count = len(session.messages)
        session.revision += 1
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._write_session(session)
