# SPDX-License-Identifier: Apache-2.0
"""Small scheduler process for verified workspace backups."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from gov_webui.backup_store import BackupError, WorkspaceBackupManager
from gov_webui.library_store import LibraryStore, LibraryStoreError
from gov_webui.ops import deployment_metadata


def run_once(
    *,
    data_root: Path,
    backup_root: Path,
    default_context_id: str,
    now: datetime | None = None,
) -> list[dict]:
    current = now or datetime.now(timezone.utc)
    library = LibraryStore(
        data_root / "marginalia" / "library.json",
        default_context_id=default_context_id,
    )
    manager = WorkspaceBackupManager(
        data_root=data_root,
        backup_root=backup_root,
        default_context_id=default_context_id,
        deployment=deployment_metadata(),
        require_remote=os.environ.get("MARGINALIA_BACKUP_REQUIRE_REMOTE", "false").lower()
        in {"true", "1", "yes"},
    )
    results = []
    for workspace in library.list_workspaces():
        if not workspace.backup_enabled or workspace.backup_schedule != "daily":
            continue
        if current.hour < workspace.backup_hour_utc:
            continue
        try:
            date_marker = current.strftime("%Y%m%dT")
            if any(date_marker in item["filename"] for item in manager.list(workspace.id)):
                continue
            results.append({"ok": True, **manager.create(workspace.id, now=current)})
        except (BackupError, OSError) as exc:
            results.append({"ok": False, "workspace_id": workspace.id, "error": str(exc)})
    return results


def main() -> int:
    data_root = Path(os.environ.get("MARGINALIA_DATA_ROOT", "/data"))
    backup_root = Path(os.environ.get("MARGINALIA_BACKUP_ROOT", "/backups"))
    context_id = os.environ.get("GOVERNOR_CONTEXT_ID", "erin-writing")
    interval = max(60, int(os.environ.get("MARGINALIA_BACKUP_POLL_SECONDS", "900")))
    print(
        json.dumps(
            {
                "event": "backup_worker_started",
                "data_root": str(data_root),
                "backup_root": str(backup_root),
                "poll_seconds": interval,
            }
        ),
        flush=True,
    )
    while True:
        next_poll = interval
        try:
            for result in run_once(
                data_root=data_root,
                backup_root=backup_root,
                default_context_id=context_id,
            ):
                print(json.dumps({"event": "backup_attempt", **result}), flush=True)
        except LibraryStoreError as exc:
            print(json.dumps({"event": "backup_worker_error", "error": str(exc)}), flush=True)
            next_poll = min(60, interval)
        time.sleep(next_poll)


if __name__ == "__main__":
    raise SystemExit(main())
