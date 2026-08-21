# SPDX-License-Identifier: Apache-2.0
"""Startup migration checks and scheduled-backup regressions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from gov_webui.artifact_store import ArtifactStore
from gov_webui.backup_worker import run_once
from gov_webui.library_store import LibraryStore
from gov_webui.ops import migration_preflight


def _write_v1_library(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_project_id": "default",
                "projects": {
                    "default": {
                        "id": "default",
                        "name": "Existing novel",
                        "context_id": "erin-writing",
                        "archived": False,
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                },
                "conversations": {},
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_preflight_requires_explicit_supported_migration(tmp_path):
    library_path = tmp_path / "marginalia" / "library.json"
    _write_v1_library(library_path)

    blocked = migration_preflight(
        data_root=tmp_path,
        default_context_id="erin-writing",
        apply_migrations=False,
    )
    applied = migration_preflight(
        data_root=tmp_path,
        default_context_id="erin-writing",
        apply_migrations=True,
    )

    assert blocked["ready"] is False
    assert blocked["migration_required"] is True
    assert json.loads(library_path.read_text())["schema_version"] == 2
    assert applied["ready"] is True
    assert applied["migration_applied"] is True
    assert applied["workspaces"] == 1


def test_preflight_rejects_a_future_schema_without_rewriting_it(tmp_path):
    path = tmp_path / "marginalia" / "library.json"
    path.parent.mkdir(parents=True)
    original = '{"schema_version":99}\n'
    path.write_text(original)

    result = migration_preflight(
        data_root=tmp_path,
        default_context_id="erin-writing",
        apply_migrations=True,
    )

    assert result["ready"] is False
    assert "newer" in result["errors"][0]
    assert path.read_text() == original


def test_daily_worker_runs_once_per_utc_day_and_manual_policy_never_runs(tmp_path):
    data_root = tmp_path / "data"
    backup_root = tmp_path / "backups"
    library = LibraryStore(
        data_root / "marginalia" / "library.json",
        default_context_id="erin-writing",
    )
    library.update_workspace(
        "erin",
        backup_enabled=True,
        backup_schedule="daily",
        backup_hour_utc=4,
    )
    now = datetime(2026, 8, 20, 5, tzinfo=timezone.utc)

    first = run_once(
        data_root=data_root,
        backup_root=backup_root,
        default_context_id="erin-writing",
        now=now,
    )
    duplicate = run_once(
        data_root=data_root,
        backup_root=backup_root,
        default_context_id="erin-writing",
        now=now,
    )
    library.update_workspace("erin", backup_schedule="manual")
    manual = run_once(
        data_root=data_root,
        backup_root=backup_root,
        default_context_id="erin-writing",
        now=datetime(2026, 8, 21, 5, tzinfo=timezone.utc),
    )

    assert len(first) == 1
    assert first[0]["ok"] is True
    assert duplicate == []
    assert manual == []


def test_preflight_checks_artifact_content_hashes(tmp_path):
    LibraryStore(
        tmp_path / "marginalia" / "library.json",
        default_context_id="erin-writing",
    )
    artifacts = ArtifactStore(
        tmp_path / ".governor" / "erin-writing" / ".governor"
    )
    artifact, _, _ = artifacts.create(
        title="Exact draft",
        content="This content is hash-bound.",
        kind="markdown",
        language="",
    )
    healthy = migration_preflight(
        data_root=tmp_path,
        default_context_id="erin-writing",
    )

    content_path = (
        tmp_path
        / ".governor"
        / "erin-writing"
        / ".governor"
        / ".governor"
        / "artifacts"
        / "content"
        / artifact.id
        / "v1.txt"
    )
    content_path.write_text("tampered")
    damaged = migration_preflight(
        data_root=tmp_path,
        default_context_id="erin-writing",
    )

    assert healthy["ready"] is True
    assert damaged["ready"] is False
    assert any("artifact content hash mismatch" in item for item in damaged["errors"])
