# SPDX-License-Identifier: Apache-2.0
"""Marginalia-owned conversation persistence with additive model provenance."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
            message_count=data.get("message_count", 0),
            messages=[
                SessionMessage.from_dict(message)
                for message in data.get("messages", [])
            ],
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

    def _write_session(self, session: ChatSession) -> None:
        self._ensure_dir()
        self._session_path(session.id).write_text(
            json.dumps(session.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

    def _read_session(self, session_id: str) -> ChatSession | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            return ChatSession.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
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
                session = ChatSession.from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
                summaries.append(session.to_summary())
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        summaries.sort(key=lambda item: item["updated_at"], reverse=True)
        return summaries

    def delete(self, session_id: str) -> bool:
        path = self._session_path(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def update_title(self, session_id: str, title: str) -> bool:
        session = self._read_session(session_id)
        if session is None:
            return False
        session.title = title
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._write_session(session)
        return True

    def update_model(self, session_id: str, model: str) -> bool:
        session = self._read_session(session_id)
        if session is None:
            return False
        session.model = model
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._write_session(session)
        return True

    def append_message(self, session_id: str, msg: SessionMessage) -> bool:
        session = self._read_session(session_id)
        if session is None:
            return False
        session.messages.append(msg)
        session.message_count = len(session.messages)
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._write_session(session)
        return True
