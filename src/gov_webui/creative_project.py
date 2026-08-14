# SPDX-License-Identifier: Apache-2.0
"""Persisted creative direction for one Marginalia context.

This is application state, not governance state.  Each store is rooted in one
AG context directory and embeds that context identity in its file so an
accidentally copied project file fails closed instead of silently leaking
creative direction between projects.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CreativeProjectError(RuntimeError):
    """Base error for creative-project persistence."""


class CreativeProjectContextMismatch(CreativeProjectError):
    """A project file belongs to a different Marginalia context."""


class CreativeProjectVersionConflict(CreativeProjectError):
    """An update was based on a stale project version."""


class CreativeProjectConfig(BaseModel):
    """The deliberately small M1 creative-project configuration."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    context_id: str
    version: int = Field(default=1, ge=1)
    project_brief: str = ""
    collaborator_stance: str = ""
    voice_style_guidance: str = ""
    created_at: str
    updated_at: str

    @property
    def has_guidance(self) -> bool:
        return any(
            value.strip()
            for value in (
                self.project_brief,
                self.collaborator_stance,
                self.voice_style_guidance,
            )
        )


class CreativeProjectStore:
    """Atomic, context-bound storage for one creative project."""

    _MAX_FIELD_LENGTH = 20_000

    def __init__(self, context_root: Path, context_id: str) -> None:
        if not context_id or context_id in {".", ".."}:
            raise ValueError("context_id must identify one project")
        self.context_root = context_root.resolve()
        self.context_id = context_id
        self.directory = self.context_root / "marginalia"
        self.path = self.directory / "project.json"
        self._lock = threading.RLock()
        self._config = self._load()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _fresh(self) -> CreativeProjectConfig:
        now = self._now()
        return CreativeProjectConfig(
            context_id=self.context_id,
            created_at=now,
            updated_at=now,
        )

    def _load(self) -> CreativeProjectConfig:
        if not self.path.exists():
            return self._fresh()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            config = CreativeProjectConfig.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise CreativeProjectError(
                f"cannot load creative project at {self.path}: {exc}"
            ) from exc
        if config.context_id != self.context_id:
            raise CreativeProjectContextMismatch(
                f"project file belongs to {config.context_id!r}, not {self.context_id!r}"
            )
        return config

    @classmethod
    def _normalize(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) > cls._MAX_FIELD_LENGTH:
            raise ValueError(
                f"creative-project fields are limited to {cls._MAX_FIELD_LENGTH} characters"
            )
        return normalized

    def get(self) -> CreativeProjectConfig:
        with self._lock:
            return self._config.model_copy(deep=True)

    def update(
        self,
        *,
        project_brief: str,
        collaborator_stance: str,
        voice_style_guidance: str,
        expected_version: int | None = None,
    ) -> CreativeProjectConfig:
        with self._lock:
            if expected_version is not None and expected_version != self._config.version:
                raise CreativeProjectVersionConflict(
                    f"expected project version {expected_version}, "
                    f"current version is {self._config.version}"
                )
            updated = self._config.model_copy(
                update={
                    "version": self._config.version + 1,
                    "project_brief": self._normalize(project_brief),
                    "collaborator_stance": self._normalize(collaborator_stance),
                    "voice_style_guidance": self._normalize(voice_style_guidance),
                    "updated_at": self._now(),
                }
            )
            self._save(updated)
            self._config = updated
            return updated.model_copy(deep=True)

    def _save(self, config: CreativeProjectConfig) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        payload = json.dumps(
            config.model_dump(mode="json"),
            indent=2,
            ensure_ascii=False,
        ) + "\n"
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise CreativeProjectError(
                f"cannot persist creative project at {self.path}: {exc}"
            ) from exc


def render_project_context(config: CreativeProjectConfig) -> dict[str, str] | None:
    """Render the one product prompt block injected before governed execution."""
    if not config.has_guidance:
        return None
    guidance: dict[str, Any] = {
        "project_brief": config.project_brief,
        "collaborator_stance": config.collaborator_stance,
        "voice_style_guidance": config.voice_style_guidance,
    }
    content = (
        "You are collaborating on a creative-writing project in Marginalia. "
        "Apply the writer's persistent project direction throughout this fiction "
        "conversation. Treat active canon and negative constraints as authoritative "
        "for continuity.\n"
        "[MARGINALIA_PROJECT_CONTEXT_V1]\n"
        f"{json.dumps(guidance, ensure_ascii=False, indent=2)}\n"
        "[/MARGINALIA_PROJECT_CONTEXT_V1]"
    )
    return {"role": "system", "content": content}
