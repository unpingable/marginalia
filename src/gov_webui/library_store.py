# SPDX-License-Identifier: Apache-2.0
"""Project and conversation lifecycle metadata for Marginalia.

The library is deliberately a sidecar. Existing conversation, creative-project,
canon, and artifact files remain authoritative for their content; this store
only adds the organization needed to keep those records usable over time.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
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


class WorkspaceNotFoundError(LibraryStoreError):
    """A requested household workspace does not exist."""


class ConversationLifecycleNotFoundError(LibraryStoreError):
    """A requested conversation has no lifecycle record."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    context_id: str
    workspace_id: str = "erin"
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


class WorkspaceRecord(BaseModel):
    """A lightweight contextual partition, deliberately not an auth boundary."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    default_project_id: str
    backup_enabled: bool = False
    backup_subdirectory: str = ""
    backup_schedule: Literal["manual", "daily"] = "manual"
    backup_hour_utc: int = Field(default=7, ge=0, le=23)
    backup_retention_count: int = Field(default=14, ge=1, le=365)
    created_at: str
    updated_at: str


class LibraryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    default_workspace_id: str = "erin"
    default_project_id: str = "default"
    workspaces: dict[str, WorkspaceRecord] = Field(default_factory=dict)
    projects: dict[str, ProjectRecord] = Field(default_factory=dict)
    conversations: dict[str, ConversationLifecycle] = Field(default_factory=dict)
    updated_at: str


class LibraryStore:
    """Atomic project/lifecycle sidecar with lazy legacy enrollment."""

    MAX_NAME_LENGTH = 160
    MAX_BACKUP_SUBDIRECTORY_LENGTH = 120

    def __init__(self, path: Path, *, default_context_id: str) -> None:
        self.path = path
        self.default_context_id = default_context_id
        self._lock = threading.RLock()
        self._state = self._load_or_create()

    @staticmethod
    def _clean_name(name: str, *, label: str = "project") -> str:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError(f"{label} name must not be empty")
        if len(cleaned) > LibraryStore.MAX_NAME_LENGTH:
            raise ValueError(
                f"{label} name is limited to {LibraryStore.MAX_NAME_LENGTH} characters"
            )
        return cleaned

    def _fresh(self) -> LibraryState:
        now = _now()
        workspace = WorkspaceRecord(
            id="erin",
            name="Erin",
            default_project_id="default",
            backup_subdirectory="erin",
            created_at=now,
            updated_at=now,
        )
        default = ProjectRecord(
            id="default",
            name="Default project",
            context_id=self.default_context_id,
            workspace_id=workspace.id,
            created_at=now,
            updated_at=now,
        )
        return LibraryState(
            workspaces={workspace.id: workspace},
            projects={default.id: default},
            updated_at=now,
        )

    @staticmethod
    def _write_text_atomic(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    def _migrate_v1(self, source: str, payload: dict) -> LibraryState:
        """Enroll the existing household data in Erin without touching content stores."""
        now = _now()
        default_project_id = payload.get("default_project_id", "default")
        projects = payload.get("projects", {})
        if default_project_id not in projects:
            raise LibraryStoreError("library default project is missing")
        default_project = projects[default_project_id]
        for project in projects.values():
            project["workspace_id"] = "erin"
        payload.update(
            {
                "schema_version": 2,
                "default_workspace_id": "erin",
                "workspaces": {
                    "erin": {
                        "id": "erin",
                        "name": "Erin",
                        "default_project_id": default_project_id,
                        "backup_enabled": False,
                        "backup_subdirectory": "erin",
                        "backup_schedule": "manual",
                        "backup_hour_utc": 7,
                        "backup_retention_count": 14,
                        "created_at": default_project.get("created_at", now),
                        "updated_at": now,
                    }
                },
            }
        )
        try:
            state = LibraryState.model_validate(payload)
        except ValidationError as exc:
            raise LibraryStoreError(f"cannot migrate library schema 1 to 2: {exc}") from exc

        source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        backup_path = self.path.with_name("library.pre-schema-2.json")
        if backup_path.exists():
            backup_hash = hashlib.sha256(backup_path.read_bytes()).hexdigest()
            if backup_hash != source_hash:
                raise LibraryStoreError(
                    "existing pre-migration library copy does not match the source"
                )
        else:
            self._write_text_atomic(backup_path, source)
        migrated = state.model_dump_json(indent=2) + "\n"
        receipt = {
            "schema": "marginalia.library-migration/v1",
            "from_schema": 1,
            "to_schema": 2,
            "migrated_at": now,
            "source_sha256": source_hash,
            "result_sha256": hashlib.sha256(migrated.encode("utf-8")).hexdigest(),
            "workspace_id": "erin",
            "projects_preserved": len(state.projects),
            "conversations_preserved": len(state.conversations),
        }
        pending_receipt = self.path.with_name("library.migration-1-2.pending.json")
        final_receipt = self.path.with_name("library.migration-1-2.json")
        self._write_text_atomic(
            pending_receipt,
            json.dumps(receipt, indent=2) + "\n",
        )
        self._write_text_atomic(self.path, migrated)
        os.replace(pending_receipt, final_receipt)
        self._fsync_directory(self.path.parent)
        return state

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _finalize_pending_migration(self, source: str) -> None:
        pending = self.path.with_name("library.migration-1-2.pending.json")
        if not pending.exists():
            return
        try:
            receipt = json.loads(pending.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LibraryStoreError(f"cannot read pending library migration: {exc}") from exc
        result_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if receipt.get("result_sha256") != result_hash:
            raise LibraryStoreError("pending library migration does not match the current library")
        os.replace(pending, self.path.with_name("library.migration-1-2.json"))
        self._fsync_directory(self.path.parent)

    def _load_or_create(self) -> LibraryState:
        if not self.path.exists():
            state = self._fresh()
            self._write(state)
            return state
        try:
            source = self.path.read_text(encoding="utf-8")
            raw = json.loads(source)
            schema_version = raw.get("schema_version", 1)
            if schema_version == 1:
                state = self._migrate_v1(source, raw)
            elif schema_version == 2:
                state = LibraryState.model_validate(raw)
                self._finalize_pending_migration(source)
            else:
                raise LibraryStoreError(f"unsupported library schema version: {schema_version}")
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            raise LibraryStoreError(f"cannot load library at {self.path}: {exc}") from exc
        if state.default_project_id not in state.projects:
            raise LibraryStoreError("library default project is missing")
        if state.default_workspace_id not in state.workspaces:
            raise LibraryStoreError("library default workspace is missing")
        if (
            state.default_project_id
            != state.workspaces[state.default_workspace_id].default_project_id
        ):
            raise LibraryStoreError("library default project does not match its workspace")
        for workspace in state.workspaces.values():
            project = state.projects.get(workspace.default_project_id)
            if project is None or project.workspace_id != workspace.id:
                raise LibraryStoreError(f"workspace {workspace.id} default project is invalid")
        for project in state.projects.values():
            if project.workspace_id not in state.workspaces:
                raise LibraryStoreError(
                    f"project {project.id} references missing workspace {project.workspace_id}"
                )
        for conversation in state.conversations.values():
            if conversation.project_id not in state.projects:
                raise LibraryStoreError(
                    f"conversation {conversation.session_id} references missing project "
                    f"{conversation.project_id}"
                )
        return state

    def _write(self, state: LibraryState) -> None:
        payload = state.model_dump_json(indent=2) + "\n"
        try:
            self._write_text_atomic(self.path, payload)
        except OSError as exc:
            raise LibraryStoreError(f"cannot persist library at {self.path}: {exc}") from exc

    def _save(self) -> None:
        self._state.updated_at = _now()
        self._write(self._state)

    def snapshot(self) -> LibraryState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def default_workspace(self) -> WorkspaceRecord:
        with self._lock:
            return self._state.workspaces[self._state.default_workspace_id].model_copy(deep=True)

    def get_workspace(self, workspace_id: str | None = None) -> WorkspaceRecord:
        with self._lock:
            resolved = workspace_id or self._state.default_workspace_id
            workspace = self._state.workspaces.get(resolved)
            if workspace is None:
                raise WorkspaceNotFoundError(f"workspace not found: {resolved}")
            return workspace.model_copy(deep=True)

    def list_workspaces(self) -> list[WorkspaceRecord]:
        with self._lock:
            workspaces = [item.model_copy(deep=True) for item in self._state.workspaces.values()]
        workspaces.sort(key=lambda item: (item.name.casefold(), item.id))
        return workspaces

    @staticmethod
    def _workspace_slug(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:48] or "workspace"

    @classmethod
    def _clean_backup_subdirectory(cls, value: str) -> str:
        cleaned = value.strip().strip("/")
        if not cleaned or cleaned in {".", ".."}:
            raise ValueError("backup subdirectory must not be empty")
        if len(cleaned) > cls.MAX_BACKUP_SUBDIRECTORY_LENGTH:
            raise ValueError(
                f"backup subdirectory is limited to {cls.MAX_BACKUP_SUBDIRECTORY_LENGTH} characters"
            )
        if "/" in cleaned or "\\" in cleaned or not re.fullmatch(r"[A-Za-z0-9._-]+", cleaned):
            raise ValueError(
                "backup subdirectory may contain letters, numbers, dot, dash, and underscore"
            )
        return cleaned

    def create_workspace(self, name: str) -> tuple[WorkspaceRecord, ProjectRecord]:
        cleaned = self._clean_name(name, label="workspace")
        with self._lock:
            base_id = self._workspace_slug(cleaned)
            workspace_id = base_id
            while workspace_id in self._state.workspaces:
                workspace_id = f"{base_id}-{uuid4().hex[:6]}"
            project_id = uuid4().hex[:12]
            suffix = f"-workspace-{workspace_id}"
            context_id = f"{self.default_context_id[: 128 - len(suffix)]}{suffix}"
            now = _now()
            project = ProjectRecord(
                id=project_id,
                name="Default project",
                context_id=context_id,
                workspace_id=workspace_id,
                created_at=now,
                updated_at=now,
            )
            workspace = WorkspaceRecord(
                id=workspace_id,
                name=cleaned,
                default_project_id=project.id,
                backup_subdirectory=self._clean_backup_subdirectory(workspace_id),
                created_at=now,
                updated_at=now,
            )
            self._state.workspaces[workspace.id] = workspace
            self._state.projects[project.id] = project
            self._save()
            return workspace.model_copy(deep=True), project.model_copy(deep=True)

    def update_workspace(
        self,
        workspace_id: str,
        *,
        name: str | None = None,
        backup_enabled: bool | None = None,
        backup_subdirectory: str | None = None,
        backup_schedule: Literal["manual", "daily"] | None = None,
        backup_hour_utc: int | None = None,
        backup_retention_count: int | None = None,
    ) -> WorkspaceRecord:
        with self._lock:
            workspace = self._state.workspaces.get(workspace_id)
            if workspace is None:
                raise WorkspaceNotFoundError(f"workspace not found: {workspace_id}")
            if name is not None:
                workspace.name = self._clean_name(name, label="workspace")
            if backup_enabled is not None:
                workspace.backup_enabled = backup_enabled
            if backup_subdirectory is not None:
                workspace.backup_subdirectory = self._clean_backup_subdirectory(backup_subdirectory)
            if backup_schedule is not None:
                if backup_schedule not in {"manual", "daily"}:
                    raise ValueError("backup schedule must be manual or daily")
                workspace.backup_schedule = backup_schedule
            if backup_hour_utc is not None:
                if not 0 <= backup_hour_utc <= 23:
                    raise ValueError("backup hour must be between 0 and 23 UTC")
                workspace.backup_hour_utc = backup_hour_utc
            if backup_retention_count is not None:
                if not 1 <= backup_retention_count <= 365:
                    raise ValueError("backup retention must be between 1 and 365")
                workspace.backup_retention_count = backup_retention_count
            workspace.updated_at = _now()
            self._save()
            return workspace.model_copy(deep=True)

    def default_project(self, workspace_id: str | None = None) -> ProjectRecord:
        with self._lock:
            workspace = self.get_workspace(workspace_id)
            return self._state.projects[workspace.default_project_id].model_copy(deep=True)

    def get_project(
        self,
        project_id: str | None = None,
        *,
        workspace_id: str | None = None,
    ) -> ProjectRecord:
        with self._lock:
            resolved = project_id or self.get_workspace(workspace_id).default_project_id
            project = self._state.projects.get(resolved)
            if project is None:
                raise ProjectNotFoundError(f"project not found: {resolved}")
            if workspace_id is not None and project.workspace_id != workspace_id:
                raise ProjectNotFoundError(
                    f"project {resolved} does not belong to workspace {workspace_id}"
                )
            return project.model_copy(deep=True)

    def list_projects(
        self,
        *,
        include_archived: bool = True,
        workspace_id: str | None = None,
    ) -> list[ProjectRecord]:
        with self._lock:
            projects = [
                project.model_copy(deep=True)
                for project in self._state.projects.values()
                if (workspace_id is None or project.workspace_id == workspace_id)
                and (include_archived or not project.archived)
            ]
        projects.sort(key=lambda item: (item.archived, item.name.casefold(), item.id))
        return projects

    def create_project(self, name: str, *, workspace_id: str | None = None) -> ProjectRecord:
        cleaned = self._clean_name(name)
        with self._lock:
            workspace = self.get_workspace(workspace_id)
            project_id = uuid4().hex[:12]
            suffix = f"-project-{project_id}"
            prefix = self.default_context_id[: 128 - len(suffix)]
            now = _now()
            project = ProjectRecord(
                id=project_id,
                name=cleaned,
                context_id=f"{prefix}{suffix}",
                workspace_id=workspace.id,
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
