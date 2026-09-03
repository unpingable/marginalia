# SPDX-License-Identifier: Apache-2.0
"""Operational provenance, migration preflight, and recovery CLI."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from gov_webui.session_store import ChatSession
from gov_webui.context_ops import ContextOperations, run_build
from gov_webui.context_summary import ContextSummaryError, ContextSummaryStore

from gov_webui.artifact_store import ArtifactIndex
from gov_webui.backup_store import BackupError, WorkspaceBackupManager
from gov_webui.canon_review_store import CanonReviewState
from gov_webui.creative_project import CreativeProjectConfig
from gov_webui.library_store import LibraryStore
from gov_webui.manuscript_store import ManuscriptState
from gov_webui.snapshot_store import SnapshotIndex


def application_version() -> str:
    try:
        return importlib.metadata.version("marginalia")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def deployment_metadata() -> dict[str, str]:
    return {
        "version": application_version(),
        "build_sha": os.environ.get("MARGINALIA_BUILD_SHA", "unknown"),
        "build_time": os.environ.get("MARGINALIA_BUILD_TIME", "unknown"),
        "image_ref": os.environ.get("MARGINALIA_IMAGE_REF", "unknown"),
        "deployment_id": os.environ.get("MARGINALIA_DEPLOYMENT_ID", "unknown"),
    }


def schema_versions() -> dict[str, int]:
    return {
        "library": 2,
        "creative_project": 1,
        "artifact_index": 1,
        "manuscript": 1,
        "canon_review": 1,
        "snapshot_index": 1,
        "workspace_backup": 1,
        "context_policy": 1,
        "context_summary": 1,
    }


def migration_preflight(
    *,
    data_root: Path,
    default_context_id: str,
    apply_migrations: bool = False,
) -> dict[str, Any]:
    """Validate known durable records and optionally apply additive migrations."""
    root = data_root.resolve()
    library_path = root / "marginalia" / "library.json"
    migration_required = False
    source_schema: int | None = None
    if library_path.exists():
        try:
            raw = json.loads(library_path.read_text(encoding="utf-8"))
            source_schema = int(raw.get("schema_version", 1))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return {
                "ready": False,
                "errors": [f"cannot read library schema: {exc}"],
                "migration_required": False,
                "schemas": schema_versions(),
            }
        if source_schema > schema_versions()["library"]:
            return {
                "ready": False,
                "errors": [f"library schema {source_schema} is newer than this application"],
                "migration_required": False,
                "schemas": schema_versions(),
            }
        migration_required = source_schema < schema_versions()["library"]
        if migration_required and not apply_migrations:
            return {
                "ready": False,
                "errors": [
                    f"library schema {source_schema} requires migration to "
                    f"{schema_versions()['library']}"
                ],
                "migration_required": True,
                "schemas": schema_versions(),
            }

    errors: list[str] = []
    checked_files = 0
    sessions = 0
    messages = 0
    loaded_session_ids: set[str] = set()
    try:
        library = LibraryStore(library_path, default_context_id=default_context_id)
        state = library.snapshot()
    except Exception as exc:
        return {
            "ready": False,
            "errors": [f"library validation failed: {exc}"],
            "migration_required": migration_required,
            "schemas": schema_versions(),
        }

    context_base = root / ".governor"
    for project in state.projects.values():
        context_root = context_base / project.context_id
        sessions_dir = context_root / "sessions"
        project_sessions: list[ChatSession] = []
        for path in sessions_dir.glob("*.json"):
            checked_files += 1
            try:
                session = ChatSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
                sessions += 1
                messages += len(session.messages)
                loaded_session_ids.add(session.id)
                project_sessions.append(session)
                lifecycle = state.conversations.get(session.id)
                if lifecycle is None or lifecycle.project_id != project.id:
                    errors.append(f"session lifecycle mismatch: {session.id}")
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                errors.append(f"invalid session {path}: {exc}")

        validators = {
            "creative project": (
                context_root / "marginalia" / "project.json",
                CreativeProjectConfig,
            ),
            "artifact index": (
                context_root / ".governor" / ".governor" / "artifacts" / "index.json",
                ArtifactIndex,
            ),
            "manuscript": (context_root / "marginalia" / "manuscript.json", ManuscriptState),
            "canon review": (context_root / "marginalia" / "canon-review.json", CanonReviewState),
            "snapshot index": (
                root / "marginalia" / "snapshots" / project.id / "index.json",
                SnapshotIndex,
            ),
        }
        for label, (path, model) in validators.items():
            if not path.exists():
                continue
            checked_files += 1
            try:
                model.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, json.JSONDecodeError) as exc:
                errors.append(f"invalid {label} {path}: {exc}")

        artifact_ids: set[str] = set()

        context_store = ContextSummaryStore(context_root)
        if context_store.policy_path.exists():
            checked_files += 1
            try:
                context_store.policy()
            except ContextSummaryError as exc:
                errors.append(f"invalid context policy: {exc}")
        for session in project_sessions:
            for label, path, loader in (
                ("context summary", context_store.summary_path(session.id), context_store.load),
                (
                    "context summary work",
                    context_store.work_path(session.id),
                    context_store.load_work,
                ),
            ):
                if not path.exists():
                    continue
                checked_files += 1
                try:
                    loader(session if label == "context summary" else session.id)
                except ContextSummaryError as exc:
                    errors.append(f"invalid {label} for session {session.id}: {exc}")
        artifact_index_path = validators["artifact index"][0]
        if artifact_index_path.exists():
            try:
                artifact_state = ArtifactIndex.model_validate_json(
                    artifact_index_path.read_text(encoding="utf-8")
                )
                artifact_ids = set(artifact_state.artifacts)
                content_root = artifact_index_path.parent / "content"
                for artifact in artifact_state.artifacts.values():
                    if artifact.current_version not in {
                        version.version for version in artifact.versions
                    }:
                        errors.append(
                            f"artifact {artifact.id} current version is not in its history"
                        )
                    for version in artifact.versions:
                        content_path = content_root / artifact.id / f"v{version.version}.txt"
                        checked_files += 1
                        content = content_path.read_text(encoding="utf-8")
                        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
                        if digest != version.content_hash:
                            errors.append(
                                f"artifact content hash mismatch: {artifact.id} v{version.version}"
                            )
            except (OSError, ValidationError, json.JSONDecodeError) as exc:
                errors.append(f"artifact content validation failed: {exc}")

        manuscript_path = validators["manuscript"][0]
        if manuscript_path.exists():
            try:
                manuscript_state = ManuscriptState.model_validate_json(
                    manuscript_path.read_text(encoding="utf-8")
                )
                for node in manuscript_state.nodes.values():
                    if node.artifact_id and node.artifact_id not in artifact_ids:
                        errors.append(
                            f"manuscript node {node.id} references missing artifact "
                            f"{node.artifact_id}"
                        )
            except (OSError, ValidationError, json.JSONDecodeError) as exc:
                errors.append(f"manuscript linkage validation failed: {exc}")

        project_path = validators["creative project"][0]
        if project_path.exists():
            try:
                config = CreativeProjectConfig.model_validate_json(
                    project_path.read_text(encoding="utf-8")
                )
                if config.context_id != project.context_id:
                    errors.append(f"creative project context mismatch: {project.id}")
            except (OSError, ValidationError, json.JSONDecodeError) as exc:
                errors.append(f"creative project ownership validation failed: {exc}")

        review_path = validators["canon review"][0]
        if review_path.exists():
            try:
                reviews = CanonReviewState.model_validate_json(
                    review_path.read_text(encoding="utf-8")
                )
                for item in reviews.items.values():
                    if item.project_id != project.id:
                        errors.append(f"canon review {item.id} belongs to another project")
            except (OSError, ValidationError, json.JSONDecodeError) as exc:
                errors.append(f"canon review ownership validation failed: {exc}")

        snapshot_path = validators["snapshot index"][0]
        if snapshot_path.exists():
            try:
                snapshot_state = SnapshotIndex.model_validate_json(
                    snapshot_path.read_text(encoding="utf-8")
                )
                for snapshot in snapshot_state.snapshots.values():
                    content_path = snapshot_path.parent / "content" / f"{snapshot.id}.json"
                    checked_files += 1
                    encoded = content_path.read_text(encoding="utf-8")
                    json.loads(encoded)
                    if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != snapshot.content_hash:
                        errors.append(f"snapshot content hash mismatch: {snapshot.id}")
            except (OSError, ValidationError, json.JSONDecodeError) as exc:
                errors.append(f"snapshot content validation failed: {exc}")

    for record in state.conversations.values():
        if record.project_id not in state.projects:
            errors.append(
                f"conversation {record.session_id} references missing project {record.project_id}"
            )
        elif record.session_id not in loaded_session_ids:
            errors.append(f"conversation content is missing: {record.session_id}")
    return {
        "ready": not errors,
        "errors": errors,
        "migration_required": migration_required,
        "migration_applied": migration_required and apply_migrations,
        "source_library_schema": source_schema,
        "schemas": schema_versions(),
        "workspaces": len(state.workspaces),
        "projects": len(state.projects),
        "sessions": sessions,
        "messages": messages,
        "checked_files": checked_files,
    }


def _manager(args: argparse.Namespace) -> WorkspaceBackupManager:
    return WorkspaceBackupManager(
        data_root=Path(args.data_root),
        backup_root=Path(args.backup_root),
        default_context_id=args.context_id,
        deployment=deployment_metadata(),
        require_remote=os.environ.get("MARGINALIA_BACKUP_REQUIRE_REMOTE", "false").lower()
        in {"true", "1", "yes"},
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m gov_webui.ops")
    parser.add_argument("--data-root", default=os.environ.get("MARGINALIA_DATA_ROOT", "/data"))
    parser.add_argument(
        "--backup-root", default=os.environ.get("MARGINALIA_BACKUP_ROOT", "/backups")
    )
    parser.add_argument(
        "--context-id", default=os.environ.get("GOVERNOR_CONTEXT_ID", "erin-writing")
    )
    parser.add_argument("--model-config", default=os.environ.get("MARGINALIA_MODEL_CONFIG", ""))
    parser.add_argument(
        "--maintenance-model",
        default=os.environ.get("MARGINALIA_CONTEXT_MAINTENANCE_MODEL", "claude-sonnet-4-20250514"),
    )
    parser.add_argument("--governor-socket", default=os.environ.get("GOVERNOR_SOCKET", ""))
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--apply-migrations", action="store_true")
    backup = commands.add_parser("backup")
    backup.add_argument("--workspace-id", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("archive")
    restore_test = commands.add_parser("restore-test")
    restore_test.add_argument("archive")
    restore = commands.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("--target-data-root", required=True)
    for name in ("context-plan", "context-build", "context-validate"):
        command = commands.add_parser(name)
        command.add_argument("--workspace-id")
        command.add_argument("--project-id")
        command.add_argument("--session-id")
    for name in ("context-activate", "context-deactivate"):
        command = commands.add_parser(name)
        command.add_argument("--workspace-id")
        command.add_argument("--project-id")
    return parser


def _context_operations(args: argparse.Namespace) -> ContextOperations:
    socket = Path(args.governor_socket) if args.governor_socket else None
    return ContextOperations(
        data_root=Path(args.data_root),
        default_context_id=args.context_id,
        model_config=Path(args.model_config) if args.model_config else None,
        maintenance_model=args.maintenance_model,
        socket_path=socket,
    )


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "preflight":
            result = migration_preflight(
                data_root=Path(args.data_root),
                default_context_id=args.context_id,
                apply_migrations=args.apply_migrations,
            )
        elif args.command == "backup":
            result = _manager(args).create(args.workspace_id)
        elif args.command == "verify":
            result = _manager(args).verify(Path(args.archive))
        elif args.command == "restore-test":
            result = _manager(args).restore_test(Path(args.archive))
        elif args.command == "restore":
            result = _manager(args).restore(
                Path(args.archive), target_data_root=Path(args.target_data_root)
            )
        else:
            operations = _context_operations(args)
            filters = {
                "workspace_id": getattr(args, "workspace_id", None),
                "project_id": getattr(args, "project_id", None),
            }
            if args.command in {"context-plan", "context-build", "context-validate"}:
                filters["session_id"] = args.session_id
            if args.command == "context-plan":
                result = operations.plan(**filters)
            elif args.command == "context-build":
                result = run_build(operations, **filters)
            elif args.command == "context-validate":
                result = operations.validate(**filters)
            else:
                result = operations.activate(
                    enabled=args.command == "context-activate",
                    **filters,
                )
    except (BackupError, ContextSummaryError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("ready", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
