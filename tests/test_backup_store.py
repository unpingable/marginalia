# SPDX-License-Identifier: Apache-2.0
"""Verified workspace backup and isolated restore regressions."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from gov_webui.session_store import SessionMessage, SessionStore

from gov_webui.context_summary import (
    ContextSummary,
    ContextSummaryStore,
    SummaryFact,
    SummaryGenerator,
    SummarySections,
    source_for,
    utc_now,
)
from gov_webui.backup_store import BackupError, WorkspaceBackupManager
from gov_webui.evidence_store import EncryptedEvidenceStore, create_keyring
from gov_webui.generation_store import GenerationStore
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

    project = manager._library().default_project()
    session = SessionStore(manager.data_root / ".governor" / project.context_id / "sessions").get(
        session_id
    )
    assert session is not None
    context_store = ContextSummaryStore(manager.data_root / ".governor" / project.context_id)
    context_store.set_enabled(True)
    context_store.save(
        ContextSummary(
            source=source_for(session, [session.messages[0]]),
            generator=SummaryGenerator(
                configured_model="claude-sonnet-4-20250514",
                provider_id="claude-code-local",
                model_id="sonnet",
            ),
            created_at=utc_now(),
            sections=SummarySections(
                observed_facts=[
                    SummaryFact(
                        text="The lantern position is preserved.",
                        evidence_message_ids=[session.messages[0].id],
                    )
                ]
            ),
        )
    )

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


def test_backup_keeps_ciphertext_and_restores_only_with_separate_key(tmp_path):
    manager, _session_id = _populated_manager(tmp_path)
    project = manager._library().default_project()
    context = manager.data_root / ".governor" / project.context_id
    generation_path = context / "marginalia" / "generation.sqlite"
    GenerationStore(generation_path).set_dispatch_enabled(project.id, True)
    keyring = tmp_path / "key-custody" / "evidence-keys.json"
    create_keyring(keyring, key_id="key-backup", key=b"b" * 32)
    evidence = EncryptedEvidenceStore(
        context / "marginalia" / "generation-evidence",
        keyring,
    )
    reference = evidence.write(
        {"content": "private restored response"},
        logical_request_id="gen-1",
        dispatch_id="dispatch-1",
        docket_attempt="attempt-1",
    )
    evidence_id = reference.reference.rsplit(":", 1)[1]

    archive_path = Path(manager.create("erin")["path"])
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        blob_name = (
            f"payload/contexts/{project.context_id}/marginalia/"
            f"generation-evidence/blobs/{evidence_id}.json"
        )
        assert blob_name in names
        assert b"private restored response" not in archive.read(blob_name)
        assert all("evidence-keys" not in name for name in names)
        assert all(not name.endswith(("-wal", "-shm")) for name in names)

    restored_root = tmp_path / "restored-with-key"
    manager.restore(archive_path, target_data_root=restored_root)
    restored_evidence = EncryptedEvidenceStore(
        restored_root / ".governor" / project.context_id / "marginalia" / "generation-evidence",
        keyring,
    )
    assert restored_evidence.read(evidence_id, actor="restore-test")["content"] == (
        "private restored response"
    )
    assert GenerationStore(
        restored_root / ".governor" / project.context_id / "marginalia" / "generation.sqlite"
    ).dispatch_enabled(project.id)


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
