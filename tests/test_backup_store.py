# SPDX-License-Identifier: Apache-2.0
"""Verified workspace backup and isolated restore regressions."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from governor.session_store import SessionMessage, SessionStore

from gov_webui.backup_store import BackupError, WorkspaceBackupManager
from gov_webui.library_store import LibraryStore


def _populated_manager(tmp_path: Path) -> tuple[WorkspaceBackupManager, str]:
    data_root = tmp_path / "data"
    backup_root = tmp_path / "backups"
    library = LibraryStore(
        data_root / "marginalia" / "library.json",
        default_context_id="erin-writing",
    )
    project = library.default_project()
    sessions = SessionStore(data_root / ".governor" / project.context_id / "sessions")
    session = sessions.create(project.context_id, title="Existing chapter")
    sessions.append_message(
        session.id,
        SessionMessage.create("user", "The lantern remains exactly where Erin left it."),
    )
    library.add_conversation(session.id, project.id)

    artifact_dir = (
        data_root / ".governor" / project.context_id / ".governor" / ".governor" / "artifacts"
    )
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "test-record.json").write_text(
        json.dumps({"content": "Persistent draft"}) + "\n",
        encoding="utf-8",
    )
    evidence = data_root / ".governor" / "evidence"
    evidence.mkdir(parents=True)
    (evidence / "trace.json").write_text('{"receipt":"preserved"}\n')

    manager = WorkspaceBackupManager(
        data_root=data_root,
        backup_root=backup_root,
        default_context_id="erin-writing",
        deployment={"build_sha": "abc123"},
    )
    return manager, session.id


def test_backup_verifies_and_survives_real_restore_rehearsal(tmp_path):
    manager, session_id = _populated_manager(tmp_path)

    created = manager.create("erin")
    path = Path(created["path"])
    verified = manager.verify(path)
    restored = manager.restore_test(path)

    assert created["verified"] is True
    assert path.with_suffix(".zip.sha256").is_file()
    assert verified["outer_checksum_verified"] is True
    assert verified["workspace_id"] == "erin"
    assert verified["project_count"] == 1
    assert verified["conversation_count"] == 1
    assert restored["restore_tested"] is True
    assert restored["sessions_loaded"] == 1
    assert restored["messages_loaded"] == 1
    with zipfile.ZipFile(path) as archive:
        library = json.loads(archive.read("payload/library.json"))
        assert session_id in library["conversations"]
        assert archive.read("payload/shared/evidence/trace.json")


def test_backup_tampering_and_nonempty_restore_are_rejected(tmp_path):
    manager, _ = _populated_manager(tmp_path)
    source = Path(manager.create("erin")["path"])
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(tampered, "w") as changed:
        for member in original.infolist():
            content = original.read(member)
            if member.filename == "payload/library.json":
                content = b"{}"
            changed.writestr(member, content)

    with pytest.raises(BackupError, match="checksum mismatch"):
        manager.verify(tampered)

    nonempty = tmp_path / "restore-target"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("do not overwrite")
    with pytest.raises(BackupError, match="must be empty"):
        manager.restore(source, target_data_root=nonempty)
    assert (nonempty / "keep.txt").read_text() == "do not overwrite"


def test_retention_removes_only_old_workspace_archives(tmp_path):
    manager, _ = _populated_manager(tmp_path)
    manager._library().update_workspace("erin", backup_retention_count=2)

    for _ in range(3):
        manager.create("erin")

    records = manager.list("erin")
    assert len(records) == 2
    assert all(Path(item["path"]).with_suffix(".zip.sha256").is_file() for item in records)


def test_remote_required_never_falls_back_to_local_disk(tmp_path):
    manager, _ = _populated_manager(tmp_path)
    manager.require_remote = True

    status = manager.backup_root_status()
    assert status["remote"] is False
    assert status["require_remote"] is True
    assert status["usable"] is False
    with pytest.raises(BackupError, match="must be a remote filesystem"):
        manager.create("erin")
