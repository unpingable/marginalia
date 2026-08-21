# SPDX-License-Identifier: Apache-2.0
"""Project and conversation lifecycle metadata for Marginalia.

The library is deliberately a sidecar. Existing conversation, creative-project,
canon, and artifact files remain authoritative for their content; this store
only adds the organization needed to keep those records usable over time.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class LibraryStoreError(RuntimeError):
    """The library sidecar could not be read or persisted."""


class ProjectNotFoundError(LibraryStoreError):
    """A requested project does not exist."""


class ConversationLifecycleNotFoundError(LibraryStoreError):
    """A requested conversation has no lifecycle record."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    context_id: str
    archived: bool = False
    created_at: str
    updated_at: str


class ConversationLifecycle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    project_id: str
    archived: bool = False
    pinned: bool = False
    parent_session_id: str | None = None
    forked_at_message_id: str | None = None
    created_at: str
    updated_at: str


class LibraryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    default_project_id: str = "default"
    projects: dict[str, ProjectRecord] = Field(default_factory=dict)
    conversations: dict[str, ConversationLifecycle] = Field(default_factory=dict)
    updated_at: str


class LibraryStore:
    """Atomic project/lifecycle sidecar with lazy legacy enrollment."""

    MAX_NAME_LENGTH = 160

    def __init__(self, path: Path, *, default_context_id: str) -> None:
        self.path = path
        self.default_context_id = default_context_id
        self._lock = threading.RLock()
        self._state = self._load_or_create()

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("project name must not be empty")
        if len(cleaned) > LibraryStore.MAX_NAME_LENGTH:
            raise ValueError(
                f"project name is limited to {LibraryStore.MAX_NAME_LENGTH} characters"
            )
        return cleaned

    def _fresh(self) -> LibraryState:
        now = _now()
        default = ProjectRecord(
            id="default",
            name="Default project",
            context_id=self.default_context_id,
            created_at=now,
            updated_at=now,
        )
        return LibraryState(projects={default.id: default}, updated_at=now)

    def _load_or_create(self) -> LibraryState:
        if not self.path.exists():
            state = self._fresh()
            self._write(state)
            return state
        try:
            state = LibraryState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            raise LibraryStoreError(f"cannot load library at {self.path}: {exc}") from exc
        if state.default_project_id not in state.projects:
            raise LibraryStoreError("library default project is missing")
        return state

    def _write(self, state: LibraryState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        payload = state.model_dump_json(indent=2) + "\n"
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise LibraryStoreError(f"cannot persist library at {self.path}: {exc}") from exc

    def _save(self) -> None:
        self._state.updated_at = _now()
        self._write(self._state)

    def snapshot(self) -> LibraryState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def default_project(self) -> ProjectRecord:
        with self._lock:
            return self._state.projects[self._state.default_project_id].model_copy(deep=True)

    def get_project(self, project_id: str | None = None) -> ProjectRecord:
        with self._lock:
            resolved = project_id or self._state.default_project_id
            project = self._state.projects.get(resolved)
            if project is None:
                raise ProjectNotFoundError(f"project not found: {resolved}")
            return project.model_copy(deep=True)

    def list_projects(self, *, include_archived: bool = True) -> list[ProjectRecord]:
        with self._lock:
            projects = [
                project.model_copy(deep=True)
                for project in self._state.projects.values()
                if include_archived or not project.archived
            ]
        projects.sort(key=lambda item: (item.archived, item.name.casefold(), item.id))
        return projects

    def create_project(self, name: str) -> ProjectRecord:
        cleaned = self._clean_name(name)
        with self._lock:
            project_id = uuid4().hex[:12]
            suffix = f"-project-{project_id}"
            prefix = self.default_context_id[: 128 - len(suffix)]
            now = _now()
            project = ProjectRecord(
                id=project_id,
                name=cleaned,
                context_id=f"{prefix}{suffix}",
                created_at=now,
                updated_at=now,
            )
            self._state.projects[project.id] = project
            self._save()
            return project.model_copy(deep=True)

    def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        archived: bool | None = None,
    ) -> ProjectRecord:
        with self._lock:
            project = self._state.projects.get(project_id)
            if project is None:
                raise ProjectNotFoundError(f"project not found: {project_id}")
            if name is not None:
                project.name = self._clean_name(name)
            if archived is not None:
                project.archived = archived
            project.updated_at = _now()
            self._save()
            return project.model_copy(deep=True)

    def sync_legacy_sessions(self, session_ids: list[str]) -> int:
        """Enroll untracked legacy sessions into the default project."""
        added = 0
        with self._lock:
            now = _now()
            for session_id in session_ids:
                if session_id in self._state.conversations:
                    continue
                self._state.conversations[session_id] = ConversationLifecycle(
                    session_id=session_id,
                    project_id=self._state.default_project_id,
                    created_at=now,
                    updated_at=now,
                )
                added += 1
            if added:
                self._save()
        return added

    def add_conversation(
        self,
        session_id: str,
        project_id: str,
        *,
        parent_session_id: str | None = None,
        forked_at_message_id: str | None = None,
    ) -> ConversationLifecycle:
        with self._lock:
            if project_id not in self._state.projects:
                raise ProjectNotFoundError(f"project not found: {project_id}")
            if session_id in self._state.conversations:
                raise ValueError(f"conversation already enrolled: {session_id}")
            now = _now()
            record = ConversationLifecycle(
                session_id=session_id,
                project_id=project_id,
                parent_session_id=parent_session_id,
                forked_at_message_id=forked_at_message_id,
                created_at=now,
                updated_at=now,
            )
            self._state.conversations[session_id] = record
            self._save()
            return record.model_copy(deep=True)

    def get_conversation(self, session_id: str) -> ConversationLifecycle:
        with self._lock:
            record = self._state.conversations.get(session_id)
            if record is None:
                raise ConversationLifecycleNotFoundError(
                    f"conversation lifecycle not found: {session_id}"
                )
            return record.model_copy(deep=True)

    def update_conversation(
        self,
        session_id: str,
        *,
        project_id: str | None = None,
        archived: bool | None = None,
        pinned: bool | None = None,
    ) -> ConversationLifecycle:
        with self._lock:
            record = self._state.conversations.get(session_id)
            if record is None:
                raise ConversationLifecycleNotFoundError(
                    f"conversation lifecycle not found: {session_id}"
                )
            if project_id is not None:
                if project_id not in self._state.projects:
                    raise ProjectNotFoundError(f"project not found: {project_id}")
                record.project_id = project_id
            if archived is not None:
                record.archived = archived
            if pinned is not None:
                record.pinned = pinned
            record.updated_at = _now()
            self._save()
            return record.model_copy(deep=True)

    def remove_conversation(self, session_id: str) -> bool:
        with self._lock:
            removed = self._state.conversations.pop(session_id, None)
            if removed is None:
                return False
            self._save()
            return True

    def conversation_counts(self) -> dict[str, int]:
        with self._lock:
            counts = {project_id: 0 for project_id in self._state.projects}
            for record in self._state.conversations.values():
                counts[record.project_id] = counts.get(record.project_id, 0) + 1
            return counts
