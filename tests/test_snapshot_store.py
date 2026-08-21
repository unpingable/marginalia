# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from gov_webui.snapshot_store import (
    ProjectSnapshotStore,
    SnapshotNotFoundError,
    SnapshotStoreError,
)


def test_snapshot_round_trip_and_restart(tmp_path: Path) -> None:
    payload = {"conversations": [{"id": "c1"}], "artifacts": [], "manuscript": {"nodes": []}}
    store = ProjectSnapshotStore(tmp_path, project_id="project-one")
    snapshot = store.create(name=" Before revision ", payload=payload)
    assert snapshot.name == "Before revision"
    assert snapshot.counts["conversations"] == 1

    restarted = ProjectSnapshotStore(tmp_path, project_id="project-one")
    meta, loaded = restarted.get(snapshot.id)
    assert meta.content_hash == snapshot.content_hash
    assert loaded == payload


def test_snapshot_integrity_and_missing_errors(tmp_path: Path) -> None:
    store = ProjectSnapshotStore(tmp_path, project_id="project-one")
    snapshot = store.create(
        name="Checkpoint",
        payload={"conversations": [], "artifacts": [], "manuscript": {"nodes": []}},
    )
    (tmp_path / "project-one" / "content" / f"{snapshot.id}.json").write_text("{}\n")
    with pytest.raises(SnapshotStoreError, match="integrity"):
        store.get(snapshot.id)
    with pytest.raises(SnapshotNotFoundError):
        store.get("missing")
