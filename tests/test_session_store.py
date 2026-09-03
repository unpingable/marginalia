# SPDX-License-Identifier: Apache-2.0
"""Conversation model-selection and provenance persistence tests."""

from __future__ import annotations

import json
from pathlib import Path

from gov_webui.session_store import SessionMessage, SessionStore, SessionWriteResult


def test_exact_provider_model_provenance_round_trips(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create("context", model="selection-a")
    generated = SessionMessage.create(
        "assistant",
        "First result",
        model="selection-a",
        provider_id="provider-a",
        model_id="upstream-a",
    )
    assert store.append_message(session.id, generated)

    loaded = store.get(session.id)
    assert loaded is not None
    assert loaded.messages[0].to_dict() == generated.to_dict()
    assert loaded.messages[0].provider_id == "provider-a"
    assert loaded.messages[0].model_id == "upstream-a"


def test_model_switch_changes_future_selection_not_history(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create("context", model="selection-a")
    assert store.append_message(
        session.id,
        SessionMessage.create(
            "assistant",
            "Before switch",
            model="selection-a",
            provider_id="provider-a",
            model_id="upstream-a",
        ),
    )

    assert store.update_model(session.id, "selection-b")
    assert store.append_message(
        session.id,
        SessionMessage.create(
            "assistant",
            "After switch",
            model="selection-b",
            provider_id="provider-b",
            model_id="upstream-b",
        ),
    )

    loaded = store.get(session.id)
    assert loaded is not None
    assert loaded.model == "selection-b"
    assert [(message.provider_id, message.model_id) for message in loaded.messages] == [
        ("provider-a", "upstream-a"),
        ("provider-b", "upstream-b"),
    ]


def test_legacy_message_without_provenance_remains_honestly_unrecorded(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "id": "legacy",
                "context_id": "context",
                "title": "Legacy",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "model": "historical-alias",
                "message_count": 1,
                "messages": [
                    {
                        "id": "old-message",
                        "role": "assistant",
                        "content": "Old result",
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "model": "historical-alias",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = SessionStore(sessions).get("legacy")
    assert loaded is not None
    assert loaded.messages[0].model == "historical-alias"
    assert loaded.messages[0].provider_id is None
    assert loaded.messages[0].model_id is None

    # A normal rewrite does not invent provenance from current configuration.
    assert SessionStore(sessions).update_title("legacy", "Still readable")
    rewritten = json.loads(path.read_text(encoding="utf-8"))
    assert "provider_id" not in rewritten["messages"][0]
    assert "model_id" not in rewritten["messages"][0]
    assert rewritten["revision"] == 1


def test_revision_cas_rejects_a_second_writer_from_the_same_snapshot(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    first_store = SessionStore(sessions)
    second_store = SessionStore(sessions)
    session = first_store.create("context", model="selection-a")
    expected_revision = session.revision

    first = [SessionMessage.create("user", "First prompt")]
    second = [SessionMessage.create("user", "Stale prompt")]
    assert (
        first_store.append_messages_if_revision(session.id, expected_revision, first)
        is SessionWriteResult.COMMITTED
    )
    assert (
        second_store.append_messages_if_revision(session.id, expected_revision, second)
        is SessionWriteResult.CONFLICT
    )

    durable = first_store.get(session.id)
    assert durable is not None
    assert durable.revision == expected_revision + 1
    assert [message.content for message in durable.messages] == ["First prompt"]


def test_direct_append_invalidates_an_in_flight_generation_revision(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session = store.create("context", model="selection-a")
    expected_revision = session.revision

    assert store.append_message(session.id, SessionMessage.create("user", "Imported turn"))
    assert (
        store.append_messages_if_revision(
            session.id,
            expected_revision,
            [SessionMessage.create("assistant", "Stale generated passage")],
        )
        is SessionWriteResult.CONFLICT
    )
    durable = store.get(session.id)
    assert durable is not None
    assert [message.content for message in durable.messages] == ["Imported turn"]


def test_move_removes_the_old_commit_target_and_preserves_the_new_state(tmp_path: Path) -> None:
    source = SessionStore(tmp_path / "source")
    target = SessionStore(tmp_path / "target")
    session = source.create("old-context", model="selection-a")
    expected_revision = session.revision
    metadata_committed = False

    def commit_metadata() -> None:
        nonlocal metadata_committed
        metadata_committed = True

    moved = source.move_to(session.id, target, "new-context", commit_metadata)

    assert moved is not None
    assert metadata_committed
    assert source.get(session.id) is None
    assert target.get(session.id) == moved
    assert moved.context_id == "new-context"
    assert moved.revision == expected_revision + 1
    assert (
        source.append_messages_if_revision(
            session.id,
            expected_revision,
            [SessionMessage.create("assistant", "Stale generated passage")],
        )
        is SessionWriteResult.NOT_FOUND
    )
