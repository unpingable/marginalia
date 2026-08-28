# SPDX-License-Identifier: Apache-2.0
"""Verified workspace backups and isolated restore rehearsal for Marginalia."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from gov_webui.session_store import ChatSession

from gov_webui.artifact_store import ArtifactStore
from gov_webui.canon_review_store import CanonReviewStore
from gov_webui.creative_project import CreativeProjectStore
from gov_webui.library_store import LibraryState, LibraryStore
from gov_webui.manuscript_store import ManuscriptStore
from gov_webui.snapshot_store import ProjectSnapshotStore


class BackupError(RuntimeError):
    """A backup could not be created, verified, or restored safely."""


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class WorkspaceBackupManager:
    """Create exact, hash-manifested archives for one contextual workspace."""

    SCHEMA = "marginalia.workspace-backup/v1"

    def __init__(
        self,
        *,
        data_root: Path,
        backup_root: Path,
        default_context_id: str,
        deployment: dict[str, str] | None = None,
        require_remote: bool = False,
    ) -> None:
        self.data_root = data_root.resolve()
        self.backup_root = backup_root.resolve()
        self.default_context_id = default_context_id
        self.deployment = deployment or {}
        self.require_remote = require_remote

    @property
    def library_path(self) -> Path:
        return self.data_root / "marginalia" / "library.json"

    def _library(self) -> LibraryStore:
        return LibraryStore(
            self.library_path,
            default_context_id=self.default_context_id,
        )

    def workspace_directory(self, workspace_id: str) -> Path:
        workspace = self._library().get_workspace(workspace_id)
        destination = (self.backup_root / workspace.backup_subdirectory).resolve()
        try:
            destination.relative_to(self.backup_root)
        except ValueError as exc:
            raise BackupError("workspace backup path escapes the configured root") from exc
        return destination

    @staticmethod
    def _read_stable(path: Path) -> bytes:
        """Read a source file only if its size/mtime remain stable across the read."""
        for _attempt in range(3):
            before = path.stat()
            content = path.read_bytes()
            after = path.stat()
            if (
                before.st_size == after.st_size == len(content)
                and before.st_mtime_ns == after.st_mtime_ns
            ):
                if path.suffix == ".json":
                    try:
                        json.loads(content)
                    except json.JSONDecodeError as exc:
                        raise BackupError(f"invalid JSON source file: {path}") from exc
                return content
        raise BackupError(f"source changed repeatedly during backup: {path}")

    def _workspace_library_bytes(self, workspace_id: str) -> tuple[bytes, list[str]]:
        library = self._library()
        state = library.snapshot()
        workspace = library.get_workspace(workspace_id)
        projects = {
            project.id: project
            for project in library.list_projects(
                include_archived=True,
                workspace_id=workspace.id,
            )
        }
        conversations = {
            record.session_id: record
            for record in state.conversations.values()
            if record.project_id in projects
        }
        subset = LibraryState(
            default_workspace_id=workspace.id,
            default_project_id=workspace.default_project_id,
            workspaces={workspace.id: workspace},
            projects=projects,
            conversations=conversations,
            updated_at=state.updated_at,
        )
        return (
            (subset.model_dump_json(indent=2) + "\n").encode("utf-8"),
            [project.context_id for project in projects.values()],
        )

    def _collect(self, workspace_id: str) -> dict[str, bytes]:
        library_bytes, context_ids = self._workspace_library_bytes(workspace_id)
        entries: dict[str, bytes] = {"payload/library.json": library_bytes}

        context_base = self.data_root / ".governor"
        for context_id in context_ids:
            root = context_base / context_id
            if not root.exists():
                continue
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                relative = path.relative_to(root).as_posix()
                entries[f"payload/contexts/{context_id}/{relative}"] = self._read_stable(path)

        snapshots_root = self.data_root / "marginalia" / "snapshots"
        state = LibraryState.model_validate_json(library_bytes)
        for project_id in state.projects:
            root = snapshots_root / project_id
            if not root.exists():
                continue
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                relative = path.relative_to(root).as_posix()
                entries[f"payload/snapshots/{project_id}/{relative}"] = self._read_stable(path)

        marginalia_root = self.data_root / "marginalia"
        operational_paths = [
            *marginalia_root.glob("library.migration-*.json"),
            *marginalia_root.glob("library.pre-schema-*.json"),
        ]
        for path in sorted(item for item in operational_paths if item.is_file()):
            entries[f"payload/operations/{path.name}"] = self._read_stable(path)

        for shared_name in ("evidence", "receipts"):
            root = context_base / shared_name
            if not root.exists():
                continue
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                relative = path.relative_to(root).as_posix()
                entries[f"payload/shared/{shared_name}/{relative}"] = self._read_stable(path)
        return entries

    def create(
        self,
        workspace_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        library = self._library()
        workspace = library.get_workspace(workspace_id)
        destination_status = self.backup_root_status()
        if self.require_remote and not destination_status["remote"]:
            raise BackupError(
                "backup destination must be a remote filesystem, but /backups "
                f"is {destination_status.get('filesystem_type') or 'unknown'}"
            )
        entries = self._collect(workspace.id)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise BackupError("backup timestamp must be timezone-aware")
        current = current.astimezone(timezone.utc)
        created_at = current.isoformat()
        stamp = current.strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"marginalia-{workspace.id}-{stamp}.zip"
        destination_dir = self.workspace_directory(workspace.id)
        try:
            destination_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BackupError(f"backup destination is not writable: {destination_dir}: {exc}") from exc
        destination = destination_dir / filename
        manifest = {
            "schema": self.SCHEMA,
            "created_at": created_at,
            "workspace": {
                "id": workspace.id,
                "name": workspace.name,
            },
            "deployment": self.deployment,
            "files": {
                name: {"sha256": _digest(content), "size": len(content)}
                for name, content in sorted(entries.items())
            },
        }
        fd, temporary_name = tempfile.mkstemp(
            dir=str(destination_dir),
            prefix=f".{filename}.",
            suffix=".tmp",
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, content in sorted(entries.items()):
                    archive.writestr(name, content)
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                )
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            archive_hash = _digest(destination.read_bytes())
            checksum = destination.with_suffix(destination.suffix + ".sha256")
            self._write_sidecar_atomic(
                checksum,
                f"{archive_hash}  {destination.name}\n",
            )
            self.verify(destination)
        except (OSError, zipfile.BadZipFile, BackupError) as exc:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            destination.with_suffix(destination.suffix + ".sha256").unlink(
                missing_ok=True
            )
            raise BackupError(f"cannot create verified backup: {exc}") from exc
        self._enforce_retention(workspace.id, workspace.backup_retention_count)
        return {
            "workspace_id": workspace.id,
            "path": str(destination),
            "filename": destination.name,
            "sha256": archive_hash,
            "created_at": created_at,
            "file_count": len(entries),
            "size": destination.stat().st_size,
            "verified": True,
        }

    @staticmethod
    def _write_sidecar_atomic(path: Path, content: str) -> None:
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

    def _enforce_retention(self, workspace_id: str, retain: int) -> None:
        archives = self.list(workspace_id)
        for record in archives[retain:]:
            path = Path(record["path"])
            path.unlink(missing_ok=True)
            path.with_suffix(path.suffix + ".sha256").unlink(missing_ok=True)

    def list(self, workspace_id: str) -> list[dict[str, Any]]:
        directory = self.workspace_directory(workspace_id)
        if not directory.exists():
            return []
        records = []
        for path in sorted(
            directory.glob(f"marginalia-{workspace_id}-*.zip"),
            reverse=True,
        ):
            records.append(
                {
                    "filename": path.name,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "created_at": datetime.fromtimestamp(
                        path.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
        return records

    def resolve_archive(self, workspace_id: str, filename: str) -> Path:
        if Path(filename).name != filename or not filename.endswith(".zip"):
            raise BackupError("invalid backup filename")
        if not filename.startswith(f"marginalia-{workspace_id}-"):
            raise BackupError("backup filename does not belong to this workspace")
        path = (self.workspace_directory(workspace_id) / filename).resolve()
        if not path.is_file():
            raise BackupError(f"backup not found: {filename}")
        return path

    def verify(self, path: Path) -> dict[str, Any]:
        try:
            checksum_path = path.with_suffix(path.suffix + ".sha256")
            outer_checksum_verified = False
            if checksum_path.exists():
                checksum_parts = checksum_path.read_text(encoding="utf-8").split()
                if len(checksum_parts) != 2 or checksum_parts[1] != path.name:
                    raise BackupError("invalid archive checksum sidecar")
                if checksum_parts[0] != _digest(path.read_bytes()):
                    raise BackupError("archive checksum does not match its sidecar")
                outer_checksum_verified = True
            with zipfile.ZipFile(path, "r") as archive:
                names = set(archive.namelist())
                if "manifest.json" not in names:
                    raise BackupError("backup manifest is missing")
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("schema") != self.SCHEMA:
                    raise BackupError("unsupported backup schema")
                expected = manifest.get("files", {})
                if set(expected) != names - {"manifest.json"}:
                    raise BackupError("backup file list does not match its manifest")
                for name, record in expected.items():
                    pure = PurePosixPath(name)
                    if pure.is_absolute() or ".." in pure.parts:
                        raise BackupError(f"unsafe backup member: {name}")
                    content = archive.read(name)
                    if len(content) != record["size"] or _digest(content) != record["sha256"]:
                        raise BackupError(f"backup checksum mismatch: {name}")
                library = LibraryState.model_validate_json(
                    archive.read("payload/library.json")
                )
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError, ValueError) as exc:
            raise BackupError(f"backup verification failed: {exc}") from exc
        return {
            "verified": True,
            "path": str(path),
            "schema": manifest["schema"],
            "workspace_id": library.default_workspace_id,
            "project_count": len(library.projects),
            "conversation_count": len(library.conversations),
            "file_count": len(expected),
            "outer_checksum_verified": outer_checksum_verified,
        }

    @staticmethod
    def _restore_member_path(target: Path, name: str) -> Path | None:
        pure = PurePosixPath(name)
        parts = pure.parts
        if parts[:1] != ("payload",):
            return None
        if parts[1:] == ("library.json",):
            return target / "marginalia" / "library.json"
        if parts[1:2] == ("contexts",) and len(parts) >= 4:
            return target / ".governor" / Path(*parts[2:])
        if parts[1:2] == ("snapshots",) and len(parts) >= 4:
            return target / "marginalia" / "snapshots" / Path(*parts[2:])
        if parts[1:2] == ("operations",) and len(parts) == 3:
            return target / "marginalia" / parts[2]
        if parts[1:2] == ("shared",) and len(parts) >= 4:
            return target / ".governor" / Path(*parts[2:])
        return None

    def restore(self, path: Path, *, target_data_root: Path) -> dict[str, Any]:
        """Restore into an empty target only; never overwrite an existing service."""
        verification = self.verify(path)
        target = target_data_root.resolve()
        if target.exists() and any(target.iterdir()):
            raise BackupError(f"restore target must be empty: {target}")
        target.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(path, "r") as archive:
                for name in archive.namelist():
                    destination = self._restore_member_path(target, name)
                    if destination is None:
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(name))
        except (OSError, zipfile.BadZipFile) as exc:
            raise BackupError(f"restore failed: {exc}") from exc
        return {**verification, "restored_to": str(target)}

    def restore_test(self, path: Path) -> dict[str, Any]:
        """Rebuild in a temporary root and parse real library/session records."""
        try:
            return self._restore_test(path)
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError(f"restore validation failed: {exc}") from exc

    def _restore_test(self, path: Path) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="marginalia-restore-test-") as directory:
            target = Path(directory)
            result = self.restore(path, target_data_root=target)
            library = LibraryStore(
                target / "marginalia" / "library.json",
                default_context_id=self.default_context_id,
            )
            state = library.snapshot()
            messages = 0
            sessions = 0
            loaded_session_ids: set[str] = set()
            artifacts = 0
            manuscript_nodes = 0
            canon_reviews = 0
            snapshots = 0
            for project in state.projects.values():
                context_root = target / ".governor" / project.context_id
                sessions_dir = context_root / "sessions"
                for session_path in sessions_dir.glob("*.json"):
                    session = ChatSession.from_dict(json.loads(session_path.read_text()))
                    sessions += 1
                    messages += len(session.messages)
                    loaded_session_ids.add(session.id)
                artifact_index = (
                    context_root / ".governor" / ".governor" / "artifacts" / "index.json"
                )
                artifact_ids: set[str] = set()
                if artifact_index.exists():
                    artifact_store = ArtifactStore(context_root / ".governor")
                    summaries, _ = artifact_store.list_all()
                    artifacts += len(summaries)
                    for summary in summaries:
                        meta, _, _ = artifact_store.get(summary.id)
                        artifact_ids.add(meta.id)
                        for version in meta.versions:
                            artifact_store.get_version(meta.id, version.version)
                        artifact_store.get_working_copy(meta.id)
                manuscript_path = context_root / "marginalia" / "manuscript.json"
                if manuscript_path.exists():
                    nodes, _ = ManuscriptStore(manuscript_path).list_nodes()
                    manuscript_nodes += len(nodes)
                    missing_artifacts = {
                        node.artifact_id
                        for node in nodes
                        if node.artifact_id and node.artifact_id not in artifact_ids
                    }
                    if missing_artifacts:
                        raise BackupError(
                            "restore has missing manuscript artifacts: "
                            + ", ".join(sorted(missing_artifacts))
                        )
                review_path = context_root / "marginalia" / "canon-review.json"
                if review_path.exists():
                    canon_reviews += len(
                        CanonReviewStore(review_path, project_id=project.id).list(
                            status="all"
                        )
                    )
                project_path = context_root / "marginalia" / "project.json"
                if project_path.exists():
                    CreativeProjectStore(context_root, project.context_id).get()
                snapshot_index = (
                    target / "marginalia" / "snapshots" / project.id / "index.json"
                )
                if snapshot_index.exists():
                    snapshot_store = ProjectSnapshotStore(
                        target / "marginalia" / "snapshots",
                        project_id=project.id,
                    )
                    project_snapshots = snapshot_store.list()
                    snapshots += len(project_snapshots)
                    for snapshot in project_snapshots:
                        snapshot_store.get(snapshot.id)
            missing_sessions = set(state.conversations) - loaded_session_ids
            if missing_sessions:
                raise BackupError(
                    "restore is missing conversation files: "
                    + ", ".join(sorted(missing_sessions))
                )
            for json_path in target.rglob("*.json"):
                json.loads(json_path.read_text(encoding="utf-8"))
            return {
                **result,
                "restore_tested": True,
                "workspace_count": len(state.workspaces),
                "sessions_loaded": sessions,
                "messages_loaded": messages,
                "artifacts_loaded": artifacts,
                "manuscript_nodes_loaded": manuscript_nodes,
                "canon_reviews_loaded": canon_reviews,
                "snapshots_loaded": snapshots,
                "untracked_session_files": len(
                    loaded_session_ids - set(state.conversations)
                ),
            }

    def backup_root_status(self) -> dict[str, Any]:
        """Report configuration/mount state without creating a probe file."""
        exists = self.backup_root.exists()
        read_only = None
        if exists:
            try:
                read_only = bool(os.statvfs(self.backup_root).f_flag & os.ST_RDONLY)
            except OSError:
                read_only = None
        mount = self._mount_info()
        remote = bool(
            mount
            and (
                mount["filesystem_type"] in {"nfs", "nfs4", "cifs", "smb3"}
                or mount["filesystem_type"].startswith("fuse.sshfs")
            )
        )
        writable = exists and os.access(self.backup_root, os.W_OK) and not read_only
        return {
            "path": str(self.backup_root),
            "exists": exists,
            "writable": writable,
            "read_only": read_only,
            "filesystem_type": mount["filesystem_type"] if mount else None,
            "mount_source": mount["source"] if mount else None,
            "remote": remote,
            "require_remote": self.require_remote,
            "usable": writable and (remote or not self.require_remote),
            "free_bytes": shutil.disk_usage(self.backup_root).free if exists else None,
        }

    def _mount_info(self) -> dict[str, str] | None:
        """Find the longest enclosing Linux mount without probing it with writes."""
        try:
            lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        candidates: list[tuple[int, dict[str, str]]] = []
        for line in lines:
            try:
                left, right = line.split(" - ", 1)
                left_fields = left.split()
                right_fields = right.split()
                mount_point = Path(
                    left_fields[4]
                    .replace("\\040", " ")
                    .replace("\\011", "\t")
                    .replace("\\134", "\\")
                )
                self.backup_root.relative_to(mount_point)
                candidates.append(
                    (
                        len(mount_point.parts),
                        {
                            "mount_point": str(mount_point),
                            "filesystem_type": right_fields[0],
                            "source": right_fields[1],
                        },
                    )
                )
            except (IndexError, ValueError):
                continue
        return max(candidates, key=lambda item: item[0])[1] if candidates else None
