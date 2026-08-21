# SPDX-License-Identifier: Apache-2.0
"""Acceptance coverage for Marginalia's long-running writing library."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from governor.context_manager import GovernorContextManager
from governor.session_store import SessionMessage, SessionStore


def _reset(adapter) -> None:
    adapter._context_manager = None
    adapter._session_store = None
    adapter._creative_project_store = None
    adapter._artifact_store = None
    adapter._library_store = None
    adapter._session_stores.clear()
    adapter._governed_chat_adapters.clear()
    adapter._creative_project_stores.clear()
    adapter._artifact_stores.clear()
    adapter._canon_review_stores.clear()
    adapter._manuscript_stores.clear()
    adapter._snapshot_stores.clear()
    adapter._pending_captures.clear()


@pytest.fixture
async def library_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import gov_webui.adapter as adapter

    _reset(adapter)
    contexts = tmp_path / "contexts"
    monkeypatch.setattr(adapter, "GOVERNOR_CONTEXTS_DIR", str(contexts))
    monkeypatch.setattr(adapter, "GOVERNOR_CONTEXT_ID", "erin-writing")
    monkeypatch.setattr(adapter, "GOVERNOR_MODE", "fiction")
    monkeypatch.setattr(adapter, "GOVERNOR_AUTH_TOKEN", "")
    adapter._context_manager = GovernorContextManager(base_dir=contexts)

    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, adapter, contexts
    _reset(adapter)


@pytest.mark.asyncio
async def test_legacy_conversations_migrate_without_content_changes(
    library_client,
) -> None:
    client, adapter, contexts = library_client
    context = adapter._context_manager.get_or_create("erin-writing", mode="fiction")
    legacy_store = SessionStore(contexts / "erin-writing" / "sessions")
    legacy = legacy_store.create("erin-writing", title="Existing ending discussion")
    message = SessionMessage.create("user", "The old work remains exactly here.")
    legacy_store.append_message(legacy.id, message)
    adapter._session_store = legacy_store

    projects = (await client.get("/v1/projects")).json()
    assert projects["default_project_id"] == "default"
    assert projects["projects"][0]["conversation_count"] == 1

    restored = (await client.get(f"/sessions/{legacy.id}")).json()
    assert restored["project_id"] == "default"
    assert restored["messages"] == [message.to_dict()]
    assert legacy_store.get(legacy.id).messages == [message]


@pytest.mark.asyncio
async def test_conversation_lifecycle_search_and_delete(library_client) -> None:
    client, _, _ = library_client
    created = (
        await client.post(
            "/sessions/",
            json={"title": "Elena ending discussion", "project_id": "default"},
        )
    ).json()
    await client.post(
        f"/sessions/{created['id']}/messages",
        json={"role": "user", "content": "A very specific lantern motif"},
    )

    renamed = (
        await client.patch(
            f"/sessions/{created['id']}",
            json={"title": "Elena ending", "pinned": True},
        )
    ).json()
    assert renamed["title"] == "Elena ending"
    assert renamed["pinned"] is True
    searched = (
        await client.get(
            "/sessions/",
            params={"project_id": "default", "q": "lantern", "view": "active"},
        )
    ).json()
    assert [item["id"] for item in searched["sessions"]] == [created["id"]]

    archived = (await client.patch(f"/sessions/{created['id']}", json={"archived": True})).json()
    assert archived["archived"] is True
    assert not (
        await client.get("/sessions/", params={"project_id": "default", "view": "active"})
    ).json()["sessions"]
    assert (
        await client.get("/sessions/", params={"project_id": "default", "view": "archived"})
    ).json()["sessions"][0]["id"] == created["id"]

    assert (await client.delete(f"/sessions/{created['id']}")).status_code == 200
    assert (await client.get(f"/sessions/{created['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_fork_project_move_artifact_provenance_and_canon_isolation(
    library_client,
) -> None:
    client, _, _ = library_client
    original = (
        await client.post(
            "/sessions/",
            json={"title": "Elena ending discussion", "project_id": "default"},
        )
    ).json()
    first = (
        await client.post(
            f"/sessions/{original['id']}/messages",
            json={"role": "user", "content": "Try the quiet ending."},
        )
    ).json()
    second = (
        await client.post(
            f"/sessions/{original['id']}/messages",
            json={"role": "assistant", "content": "Elena puts out the lantern."},
        )
    ).json()
    await client.post(
        f"/sessions/{original['id']}/messages",
        json={"role": "user", "content": "Now another thought."},
    )

    forked_response = await client.post(
        f"/sessions/{original['id']}/fork",
        json={
            "title": "Elena ending - darker version",
            "message_id": second["id"],
        },
    )
    assert forked_response.status_code == 201
    forked = forked_response.json()
    assert forked["parent_session_id"] == original["id"]
    assert forked["forked_at_message_id"] == second["id"]
    assert [item["id"] for item in forked["messages"]] == [first["id"], second["id"]]
    assert (await client.get(f"/sessions/{original['id']}")).json()["message_count"] == 3

    new_project = (await client.post("/v1/projects", json={"name": "Second novel"})).json()
    moved = (
        await client.patch(f"/sessions/{forked['id']}", json={"project_id": new_project["id"]})
    ).json()
    assert moved["project_id"] == new_project["id"]
    assert (
        await client.get("/sessions/", params={"project_id": new_project["id"], "view": "active"})
    ).json()["sessions"][0]["id"] == forked["id"]

    artifact_response = await client.post(
        "/governor/artifacts",
        json={
            "title": "Quiet ending",
            "content": "Elena puts out the lantern.",
            "kind": "markdown",
            "artifact_type": "draft",
            "project_id": "default",
            "source": "promote",
            "message_id": second["id"],
            "conversation_id": original["id"],
            "source_message_ids": [second["id"]],
        },
    )
    assert artifact_response.status_code == 201
    artifact = artifact_response.json()["artifact"]
    assert artifact["artifact_type"] == "draft"
    assert artifact["project_id"] == "default"
    assert artifact["provenance"]["conversation_id"] == original["id"]
    assert artifact["provenance"]["message_ids"] == [second["id"]]
    assert artifact["provenance"]["captured_at"]

    await client.post(
        "/governor/fiction/world-rules",
        json={"rule": "Lanterns cannot burn in rain.", "project_id": "default"},
    )
    default_rules = (
        await client.get("/governor/fiction/world-rules", params={"project_id": "default"})
    ).json()["rules"]
    second_rules = (
        await client.get(
            "/governor/fiction/world-rules",
            params={"project_id": new_project["id"]},
        )
    ).json()["rules"]
    assert [item["rule"] for item in default_rules] == ["Lanterns cannot burn in rain."]
    assert second_rules == []


@pytest.mark.asyncio
async def test_lightweight_workspaces_and_artifact_canon_control_plane(
    library_client,
) -> None:
    client, _, _ = library_client
    original = (await client.get("/v1/workspaces")).json()
    assert original["default_workspace_id"] == "erin"
    assert original["workspaces"][0]["name"] == "Erin"

    james = (await client.post("/v1/workspaces", json={"name": "James"})).json()
    assert james["name"] == "James"
    assert james["default_project"]["workspace_id"] == james["id"]
    assert (await client.get("/v1/projects", params={"workspace_id": "erin"})).json()[
        "projects"
    ][0]["id"] == "default"
    james_projects = (
        await client.get("/v1/projects", params={"workspace_id": james["id"]})
    ).json()["projects"]
    assert {item["workspace_id"] for item in james_projects} == {james["id"]}

    artifact = (
        await client.post(
            "/governor/artifacts",
            json={
                "title": "Lantern consequence",
                "content": "Elena lets the forbidden lantern burn all night.",
                "kind": "markdown",
                "artifact_type": "scene",
                "project_id": "default",
            },
        )
    ).json()["artifact"]
    await client.post(
        "/governor/fiction/forbidden",
        json={
            "description": "The lantern must never burn overnight.",
            "patterns": ["burn all night"],
            "project_id": "default",
        },
    )

    comparison = (
        await client.get(
            f"/governor/artifacts/{artifact['id']}/canon-comparison",
            params={"project_id": "default"},
        )
    ).json()
    assert comparison["canonical_content_changed"] is False
    assert comparison["continuity"]["passed"] is False
    assert comparison["continuity"]["violations"][0]["anchor_type"] == "prohibition"

    rules_before = (
        await client.get(
            "/governor/fiction/world-rules", params={"project_id": "default"}
        )
    ).json()["rules"]
    proposal_response = await client.post(
        f"/governor/artifacts/{artifact['id']}/canon-proposal",
        json={
            "project_id": "default",
            "kind": "world_rule",
            "statement": "Lantern smoke reveals hidden doors.",
        },
    )
    assert proposal_response.status_code == 201
    proposal = proposal_response.json()
    assert proposal["canonical"] is False
    assert proposal["candidate"]["draft"]["artifact_id"] == artifact["id"]
    assert proposal["candidate"]["draft"]["artifact_version"] == 1
    assert (
        await client.get(
            "/governor/fiction/world-rules", params={"project_id": "default"}
        )
    ).json()["rules"] == rules_before

    accepted = await client.post(
        f"/governor/fiction/capture/{proposal['candidate']['id']}/accept",
        json={
            "capture_type": "world_rule",
            "description": proposal["candidate"]["statement"],
            "project_id": "default",
        },
    )
    assert accepted.status_code == 200
    rules_after = (
        await client.get(
            "/governor/fiction/world-rules", params={"project_id": "default"}
        )
    ).json()["rules"]
    assert len(rules_after) == len(rules_before) + 1


@pytest.mark.asyncio
async def test_workspace_backup_api_and_operational_provenance(
    library_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, adapter, contexts = library_client
    data_root = contexts.parent
    backup_root = data_root / "backups"
    monkeypatch.setattr(adapter, "MARGINALIA_DATA_ROOT", str(data_root))
    monkeypatch.setattr(adapter, "MARGINALIA_BACKUP_ROOT", str(backup_root))

    policy = await client.patch(
        "/v1/workspaces/erin",
        json={
            "backup_enabled": True,
            "backup_schedule": "daily",
            "backup_hour_utc": 6,
            "backup_retention_count": 7,
            "backup_subdirectory": "erin-novel",
        },
    )
    assert policy.status_code == 200
    assert policy.json()["backup_subdirectory"] == "erin-novel"

    created_response = await client.post("/v1/workspaces/erin/backups")
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["verified"] is True
    listing = (await client.get("/v1/workspaces/erin/backups")).json()
    assert listing["destination"]["writable"] is True
    assert listing["backups"][0]["filename"] == created["filename"]

    verify = await client.post(
        f"/v1/workspaces/erin/backups/{created['filename']}/verify"
    )
    rehearse = await client.post(
        f"/v1/workspaces/erin/backups/{created['filename']}/restore-test"
    )
    assert verify.json()["outer_checksum_verified"] is True
    assert rehearse.json()["restore_tested"] is True

    system = (await client.get("/v1/system")).json()
    assert system["service"] == "marginalia"
    assert system["schemas"]["library"] == 2
    assert system["migration"]["ready"] is True
    assert system["deployment"]["version"]


@pytest.mark.asyncio
async def test_manuscript_and_canon_review_foundations_survive_restart(
    library_client,
) -> None:
    client, adapter, _ = library_client
    artifact = (
        await client.post(
            "/governor/artifacts",
            json={
                "title": "Opening scene",
                "content": "Elena reaches the station.",
                "artifact_type": "scene",
                "project_id": "default",
                "status": "drafting",
                "tags": ["Elena", "Opening"],
            },
        )
    ).json()["artifact"]
    assert artifact["status"] == "drafting"
    assert artifact["tags"] == ["Elena", "Opening"]
    chapter = (
        await client.post(
            "/v1/manuscript",
            json={
                "kind": "chapter",
                "title": "Arrival",
                "project_id": "default",
            },
        )
    ).json()
    scene = (
        await client.post(
            "/v1/manuscript",
            json={
                "kind": "scene",
                "title": "At the station",
                "parent_id": chapter["id"],
                "artifact_id": artifact["id"],
                "status": "drafting",
                "project_id": "default",
            },
        )
    ).json()

    scan = await client.post(
        "/governor/fiction/capture/scan",
        json={
            "text": "Rule: Trains never run after midnight.",
            "conversation_id": "conversation-source",
            "message_id": "message-source",
            "project_id": "default",
        },
    )
    assert scan.status_code == 200
    captures = scan.json()["captures"]
    assert captures

    adapter._manuscript_stores.clear()
    adapter._canon_review_stores.clear()
    manuscript = (
        await client.get("/v1/manuscript", params={"project_id": "default"})
    ).json()
    pending = (
        await client.get(
            "/governor/fiction/captures",
            params={"project_id": "default", "status": "pending"},
        )
    ).json()
    assert {item["id"] for item in manuscript["nodes"]} == {
        chapter["id"],
        scene["id"],
    }
    assert {item["id"] for item in pending["captures"]} == {
        item["id"] for item in captures
    }
    assert all(
        item["conversation_id"] == "conversation-source"
        for item in pending["captures"]
    )

    lifecycle = (
        await client.patch(
            f"/governor/artifacts/{artifact['id']}",
            params={"project_id": "default"},
            json={"status": "revised", "tags": ["Opening"], "trashed": True},
        )
    ).json()["artifact"]
    assert lifecycle["current_version"] == 1
    assert lifecycle["status"] == "revised"
    assert lifecycle["tags"] == ["Opening"]
    assert lifecycle["trashed_at"]

    compiled = (
        await client.get(
            "/v1/manuscript/compile",
            params={"project_id": "default", "format": "markdown"},
        )
    ).json()
    assert compiled["markdown"] == (
        "## Arrival\n\n### At the station\n\nElena reaches the station.\n"
    )
    assert compiled["word_count"] == 4
    assert compiled["missing_artifact_ids"] == []

    docx_response = await client.get(
        "/v1/manuscript/compile",
        params={"project_id": "default", "format": "docx"},
    )
    assert docx_response.status_code == 200
    assert docx_response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    with zipfile.ZipFile(io.BytesIO(docx_response.content)) as archive:
        document = archive.read("word/document.xml").decode()
    assert "At the station" in document
    assert "Elena reaches the station." in document


@pytest.mark.asyncio
async def test_project_bundle_and_named_snapshot_are_complete(library_client) -> None:
    client, adapter, _ = library_client
    conversation = (
        await client.post(
            "/sessions/",
            json={"title": "Bundle conversation", "project_id": "default"},
        )
    ).json()
    await client.post(
        f"/sessions/{conversation['id']}/messages",
        json={"role": "user", "content": "Keep the lighthouse visible."},
    )
    artifact = (
        await client.post(
            "/governor/artifacts",
            json={
                "title": "Lighthouse scene",
                "content": "The lighthouse remained visible.",
                "artifact_type": "scene",
                "project_id": "default",
                "tags": ["lighthouse"],
            },
        )
    ).json()["artifact"]
    await client.post(
        "/v1/manuscript",
        json={
            "kind": "scene",
            "title": "The light",
            "artifact_id": artifact["id"],
            "project_id": "default",
        },
    )

    exported = (await client.get("/v1/project/export")).json()
    assert exported["schema"] == "marginalia.creative-project-export/v1"
    assert exported["conversations"][0]["lifecycle"]["project_id"] == "default"
    assert exported["artifacts"][0]["tags"] == ["lighthouse"]
    assert len(exported["manuscript"]["nodes"]) == 1

    bundle = await client.get("/v1/project/export.zip")
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        names = set(archive.namelist())
        assert {"manuscript.md", "canon.md", "marginalia-project.json"} <= names
        assert "The lighthouse remained visible." in archive.read("manuscript.md").decode()

    created = await client.post(
        "/v1/project/snapshots",
        json={"name": "Before the ending", "project_id": "default"},
    )
    assert created.status_code == 201
    snapshot = created.json()
    assert snapshot["counts"] == {
        "conversations": 1,
        "artifacts": 1,
        "manuscript_nodes": 1,
    }
    adapter._snapshot_stores.clear()
    listed = (await client.get("/v1/project/snapshots")).json()["snapshots"]
    assert [item["id"] for item in listed] == [snapshot["id"]]
    restored = (await client.get(f"/v1/project/snapshots/{snapshot['id']}")).json()
    assert restored["payload"]["conversations"][0]["title"] == "Bundle conversation"


@pytest.mark.asyncio
async def test_artifact_autosave_search_compare_restore_and_trash(library_client) -> None:
    client, _, _ = library_client
    artifact = (
        await client.post(
            "/governor/artifacts",
            json={
                "title": "Storm scene",
                "content": "The rain began.",
                "artifact_type": "scene",
                "project_id": "default",
                "tags": ["weather"],
            },
        )
    ).json()["artifact"]
    autosaved = await client.put(
        f"/governor/artifacts/{artifact['id']}/working-copy",
        params={"project_id": "default"},
        json={"content": "The impossible rain began.", "base_version": 1},
    )
    assert autosaved.status_code == 200
    detail = (
        await client.get(
            f"/governor/artifacts/{artifact['id']}", params={"project_id": "default"}
        )
    ).json()
    assert detail["content"] == "The rain began."
    assert detail["working_copy"] == "The impossible rain began."
    assert detail["artifact"]["current_version"] == 1

    updated = (
        await client.put(
            f"/governor/artifacts/{artifact['id']}",
            params={"project_id": "default"},
            json={
                "content": "The impossible rain began.",
                "expected_current_version": 1,
                "source": "edit",
            },
        )
    ).json()
    assert updated["artifact"]["current_version"] == 2
    assert (
        await client.get(
            f"/governor/artifacts/{artifact['id']}", params={"project_id": "default"}
        )
    ).json()["working_copy"] is None

    compared = (
        await client.get(
            f"/governor/artifacts/{artifact['id']}/compare",
            params={"project_id": "default", "from_version": 1, "to_version": 2},
        )
    ).json()
    assert "-The rain began." in compared["diff"]
    assert "+The impossible rain began." in compared["diff"]

    restored = (
        await client.post(
            f"/governor/artifacts/{artifact['id']}/version/1/restore",
            params={"project_id": "default"},
            json={"expected_current_version": 2},
        )
    ).json()
    assert restored["artifact"]["current_version"] == 3
    assert restored["content"] == "The rain began."
    assert restored["artifact"]["versions"][-1]["source"] == "restore"

    searched = (
        await client.get(
            "/governor/artifacts",
            params={"project_id": "default", "view": "active", "q": "rain"},
        )
    ).json()
    assert [item["id"] for item in searched["artifacts"]] == [artifact["id"]]
    await client.patch(
        f"/governor/artifacts/{artifact['id']}",
        params={"project_id": "default"},
        json={"trashed": True},
    )
    assert not (
        await client.get(
            "/governor/artifacts", params={"project_id": "default", "view": "active"}
        )
    ).json()["artifacts"]
    assert (
        await client.get(
            "/governor/artifacts", params={"project_id": "default", "view": "trash"}
        )
    ).json()["artifacts"][0]["id"] == artifact["id"]


@pytest.mark.asyncio
async def test_unified_search_backlinks_and_editable_canon_review(library_client) -> None:
    client, _, _ = library_client
    conversation = (
        await client.post(
            "/sessions/",
            json={"title": "Elena at the harbor", "project_id": "default"},
        )
    ).json()
    source_message = (
        await client.post(
            f"/sessions/{conversation['id']}/messages",
            json={"role": "assistant", "content": "Elena watches the lighthouse beam."},
        )
    ).json()
    artifact = (
        await client.post(
            "/governor/artifacts",
            json={
                "title": "Harbor note",
                "content": "Elena cannot see the hidden reef.",
                "artifact_type": "note",
                "project_id": "default",
            },
        )
    ).json()["artifact"]
    await client.post(
        "/governor/fiction/characters",
        json={"name": "Elena", "description": "A keeper", "project_id": "default"},
    )
    await client.post(
        "/governor/fiction/world-rules",
        json={"rule": "The hidden reef moves at dawn.", "project_id": "default"},
    )

    search = (
        await client.get("/v1/search", params={"project_id": "default", "q": "lighthouse"})
    ).json()
    assert search["total"] >= 1
    message_result = next(item for item in search["results"] if item["kind"] == "message")
    assert message_result["conversation_id"] == conversation["id"]
    assert message_result["message_id"] == source_message["id"]

    entities = (
        await client.get("/v1/entities", params={"project_id": "default"})
    ).json()["entities"]
    elena = next(item for item in entities if item["name"] == "Elena")
    assert elena["reference_count"] >= 2
    assert {item["kind"] for item in elena["references"]} >= {"message", "artifact"}
    assert any(item["artifact_id"] == artifact["id"] for item in elena["references"])

    scan = (
        await client.post(
            "/governor/fiction/capture/scan",
            json={
                "text": "Rule: The lighthouse goes dark at noon.",
                "conversation_id": conversation["id"],
                "message_id": source_message["id"],
                "project_id": "default",
            },
        )
    ).json()
    candidate = scan["captures"][0]
    edited = (
        await client.patch(
            f"/governor/fiction/capture/{candidate['id']}",
            json={
                "statement": "The lighthouse goes dark only at noon.",
                "project_id": "default",
            },
        )
    ).json()
    assert edited["status"] == "pending"
    assert edited["statement"] == "The lighthouse goes dark only at noon."


@pytest.mark.asyncio
async def test_conversation_branch_tree_preserves_explicit_lineage(library_client) -> None:
    client, _, _ = library_client
    original = (
        await client.post(
            "/sessions/",
            json={"title": "Ending discussion", "project_id": "default"},
        )
    ).json()
    message = (
        await client.post(
            f"/sessions/{original['id']}/messages",
            json={"role": "user", "content": "Try the bright ending now."},
        )
    ).json()
    fork = (
        await client.post(
            f"/sessions/{original['id']}/fork",
            json={"title": "Darker ending", "message_id": message["id"]},
        )
    ).json()
    tree = (
        await client.get("/v1/conversations/tree", params={"project_id": "default"})
    ).json()
    nodes = {item["id"]: item for item in tree["nodes"]}
    assert tree["roots"] == [original["id"]]
    assert nodes[original["id"]]["child_session_ids"] == [fork["id"]]
    assert nodes[fork["id"]]["parent_session_id"] == original["id"]
    assert nodes[fork["id"]]["forked_at_message_id"] == message["id"]
    assert nodes[fork["id"]]["word_count"] == 5
