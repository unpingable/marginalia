# SPDX-License-Identifier: Apache-2.0
"""Marginalia-owned state layout with a tested previous-image compatibility seam."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LAYOUT_SCHEMA = "marginalia.state-layout/v1"


class StateLayoutError(RuntimeError):
    """State cannot be located or migrated without ambiguity."""


def application_root(data_root: Path) -> Path:
    return data_root / ".marginalia"


def contexts_root(data_root: Path) -> Path:
    return application_root(data_root) / "contexts"


def shared_root(data_root: Path) -> Path:
    return application_root(data_root) / "shared"


def discovered_contexts_root(data_root: Path) -> Path:
    """Locate state for a read-only pre-migration check."""
    current = contexts_root(data_root)
    legacy = data_root / ".governor"
    return legacy if not current.exists() and legacy.exists() else current


def discovered_shared_root(data_root: Path) -> Path:
    """Locate shared state for a read-only pre-migration check."""
    current = shared_root(data_root)
    legacy = data_root / "marginalia"
    return legacy if not current.exists() and legacy.exists() else current


def _same_target(link: Path, target: Path) -> bool:
    return link.is_symlink() and link.resolve() == target.resolve()


def _migrate_directory(source: Path, target: Path) -> str:
    if target.exists():
        if source.exists() and not _same_target(source, target):
            raise StateLayoutError(f"both legacy and Marginalia state exist: {source} and {target}")
        return "already_migrated"
    if source.is_symlink():
        raise StateLayoutError(
            f"legacy state link does not resolve to the expected target: {source}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        os.replace(source, target)
        action = "moved"
    else:
        target.mkdir(parents=True)
        action = "created"
    os.symlink(target.relative_to(source.parent), source, target_is_directory=True)
    return action


def migrate_state_layout(data_root: Path) -> dict[str, Any]:
    """Move state once and retain write-through links for the previous image."""
    root = data_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    app = application_root(root)
    app.mkdir(mode=0o700, exist_ok=True)
    os.chmod(app, 0o700)
    contexts = contexts_root(root)
    shared = shared_root(root)
    context_action = _migrate_directory(root / ".governor", contexts)
    shared_action = _migrate_directory(root / "marginalia", shared)
    marker = app / "state-layout.json"
    document = {
        "schema": LAYOUT_SCHEMA,
        "contexts": str(contexts),
        "shared": str(shared),
        "legacy_context_link": str(root / ".governor"),
        "legacy_shared_link": str(root / "marginalia"),
        "migrated_at": datetime.now(timezone.utc).isoformat(),
    }
    if marker.exists():
        try:
            existing = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateLayoutError("state layout marker is unreadable") from exc
        if existing.get("schema") != LAYOUT_SCHEMA:
            raise StateLayoutError("state layout marker has an unsupported schema")
    else:
        temporary = marker.with_suffix(".tmp")
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
    return {
        "ready": True,
        "schema": LAYOUT_SCHEMA,
        "contexts": context_action,
        "shared": shared_action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate"])
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(os.environ.get("MARGINALIA_DATA_ROOT", "/data")),
    )
    arguments = parser.parse_args()
    try:
        result = migrate_state_layout(arguments.data_root)
    except StateLayoutError as exc:
        print(json.dumps({"ready": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
