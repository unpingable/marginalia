# SPDX-License-Identifier: Apache-2.0
"""Project and conversation lifecycle sidecar regressions."""

from __future__ import annotations

import hashlib
import json

from gov_webui.library_store import LibraryStore


def test_legacy_sessions_enroll_in_default_project_without_touching_content(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    original = sessions / "legacy.json"
    original.write_text('{"old":"content"}\n', encoding="utf-8")

    store = LibraryStore(
        tmp_path / "marginalia" / "library.json",
        default_context_id="erin-writing",
    )
    assert store.sync_legacy_sessions(["legacy"]) == 1
    assert store.sync_legacy_sessions(["legacy"]) == 0

    project = store.default_project()
    lifecycle = store.get_conversation("legacy")
    assert project.name == "Default project"
    assert project.context_id == "erin-writing"
    assert lifecycle.project_id == project.id
    assert lifecycle.archived is False
    assert lifecycle.pinned is False
    assert original.read_text(encoding="utf-8") == '{"old":"content"}\n'


def test_projects_and_conversation_lifecycle_round_trip(tmp_path):
    path = tmp_path / "marginalia" / "library.json"
    store = LibraryStore(path, default_context_id="erin-writing")
    darker = store.create_project("  Elena darker ending  ")
    store.add_conversation(
        "fork-1",
        darker.id,
        parent_session_id="original-1",
        forked_at_message_id="msg-7",
    )
    store.update_conversation("fork-1", archived=True, pinned=True)
    store.update_project(darker.id, name="Elena alternate", archived=True)

    restarted = LibraryStore(path, default_context_id="erin-writing")
    project = restarted.get_project(darker.id)
    lifecycle = restarted.get_conversation("fork-1")

    assert project.name == "Elena alternate"
    assert project.archived is True
    assert lifecycle.project_id == darker.id
    assert lifecycle.archived is True
    assert lifecycle.pinned is True
    assert lifecycle.parent_session_id == "original-1"
    assert lifecycle.forked_at_message_id == "msg-7"


def test_project_contexts_are_isolated_and_stable(tmp_path):
    path = tmp_path / "marginalia" / "library.json"
    store = LibraryStore(path, default_context_id="erin-writing")
    first = store.create_project("First")
    second = store.create_project("Second")

    assert first.context_id != second.context_id
    assert first.context_id.startswith("erin-writing-project-")
    assert second.context_id.startswith("erin-writing-project-")
    assert len(first.context_id) <= 128
    assert (
        LibraryStore(path, default_context_id="erin-writing").get_project(first.id).context_id
        == first.context_id
    )


def test_schema_one_migrates_everything_into_erin_with_receipt(tmp_path):
    path = tmp_path / "marginalia" / "library.json"
    path.parent.mkdir()
    original_payload = {
        "schema_version": 1,
        "default_project_id": "default",
        "projects": {
            "default": {
                "id": "default",
                "name": "Existing novel",
                "context_id": "erin-writing",
                "archived": False,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-02T00:00:00+00:00",
            },
            "other": {
                "id": "other",
                "name": "Other work",
                "context_id": "erin-writing-project-other",
                "archived": True,
                "created_at": "2026-01-03T00:00:00+00:00",
                "updated_at": "2026-01-04T00:00:00+00:00",
            },
        },
        "conversations": {
            "session-1": {
                "session_id": "session-1",
                "project_id": "other",
                "archived": False,
                "pinned": True,
                "parent_session_id": None,
                "forked_at_message_id": None,
                "created_at": "2026-01-05T00:00:00+00:00",
                "updated_at": "2026-01-06T00:00:00+00:00",
            }
        },
        "updated_at": "2026-01-06T00:00:00+00:00",
    }
    original = json.dumps(original_payload, indent=2) + "\n"
    path.write_text(original, encoding="utf-8")

    store = LibraryStore(path, default_context_id="erin-writing")
    state = store.snapshot()

    assert state.schema_version == 2
    assert state.default_workspace_id == "erin"
    assert set(state.workspaces) == {"erin"}
    assert {project.workspace_id for project in state.projects.values()} == {"erin"}
    assert set(state.projects) == {"default", "other"}
    assert state.conversations["session-1"].project_id == "other"
    assert state.conversations["session-1"].pinned is True
    assert path.with_name("library.pre-schema-2.json").read_text() == original
    receipt_path = path.with_name("library.migration-1-2.json")
    receipt = json.loads(receipt_path.read_text())
    assert receipt["source_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert receipt["projects_preserved"] == 2
    assert receipt["conversations_preserved"] == 1

    pending = path.with_name("library.migration-1-2.pending.json")
    receipt_path.replace(pending)
    LibraryStore(path, default_context_id="erin-writing")
    assert receipt_path.is_file()
    assert not pending.exists()


def test_workspaces_are_lightweight_contextual_partitions(tmp_path):
    path = tmp_path / "marginalia" / "library.json"
    store = LibraryStore(path, default_context_id="erin-writing")

    workspace, default_project = store.create_workspace(" James ")
    second_project = store.create_project("Protocol novel", workspace_id=workspace.id)
    conversation = store.add_conversation("session-j", second_project.id)
    configured = store.update_workspace(
        workspace.id,
        backup_enabled=True,
        backup_subdirectory="james-writing",
        backup_schedule="daily",
        backup_hour_utc=4,
        backup_retention_count=9,
    )

    assert workspace.name == "James"
    assert default_project.workspace_id == workspace.id
    assert second_project.workspace_id == workspace.id
    assert conversation.project_id == second_project.id
    assert {item.id for item in store.list_projects(workspace_id="erin")} == {"default"}
    assert {item.id for item in store.list_projects(workspace_id=workspace.id)} == {
        default_project.id,
        second_project.id,
    }
    assert configured.backup_enabled is True
    assert configured.backup_subdirectory == "james-writing"
    assert configured.backup_schedule == "daily"
