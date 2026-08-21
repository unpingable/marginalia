# SPDX-License-Identifier: Apache-2.0
"""Project and conversation lifecycle sidecar regressions."""

from __future__ import annotations

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
