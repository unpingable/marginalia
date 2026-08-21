# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from gov_webui.canon_review_store import (
    CanonReviewNotFoundError,
    CanonReviewStore,
)


def test_candidates_survive_restart_with_source_metadata(tmp_path: Path) -> None:
    path = tmp_path / "canon-review.json"
    store = CanonReviewStore(path, project_id="novel")
    item = store.add(
        kind="character",
        confidence=0.91,
        subject="Elena",
        statement="Elena refuses to carry a gun.",
        conversation_id="conversation-1",
        message_id="message-2",
        spans=[[0, 34]],
        draft={"name": "Elena"},
    )

    restored = CanonReviewStore(path, project_id="novel")
    loaded = restored.get(item.id)
    assert loaded == item
    assert loaded.status == "pending"
    assert loaded.conversation_id == "conversation-1"
    assert loaded.message_id == "message-2"


def test_resolutions_are_durable_and_not_repeatable(tmp_path: Path) -> None:
    path = tmp_path / "canon-review.json"
    store = CanonReviewStore(path, project_id="novel")
    accepted = store.add(kind="world_rule", statement="Magic leaves ash.")
    dismissed = store.add(kind="note", statement="Maybe the moon is green.")

    store.resolve(accepted.id, status="accepted", promoted_to="world-1")
    store.resolve(dismissed.id, status="dismissed")

    restored = CanonReviewStore(path, project_id="novel")
    assert restored.list(status="pending") == []
    assert restored.get(accepted.id).promoted_to == "world-1"
    assert restored.get(dismissed.id).status == "dismissed"
    with pytest.raises(ValueError, match="already accepted"):
        restored.resolve(accepted.id, status="dismissed")


def test_sequence_and_project_scope_are_stable(tmp_path: Path) -> None:
    path = tmp_path / "canon-review.json"
    first = CanonReviewStore(path, project_id="novel")
    assert first.add(kind="note", statement="One").id == "cap-1"
    second = CanonReviewStore(path, project_id="novel")
    assert second.add(kind="note", statement="Two").id == "cap-2"
    assert all(item.project_id == "novel" for item in second.list(status="all"))


def test_missing_candidate_is_explicit(tmp_path: Path) -> None:
    store = CanonReviewStore(tmp_path / "canon-review.json", project_id="novel")
    with pytest.raises(CanonReviewNotFoundError):
        store.get("cap-404")


def test_pending_candidate_can_be_edited_but_resolved_candidate_cannot(tmp_path: Path) -> None:
    store = CanonReviewStore(tmp_path / "canon-review.json", project_id="novel")
    candidate = store.add(kind="character", subject="Elna", statement="Elna is a pilot.")
    updated = store.update(
        candidate.id,
        subject="Elena",
        statement="Elena is a lighthouse keeper.",
    )
    assert updated.subject == "Elena"
    assert updated.statement == "Elena is a lighthouse keeper."
    store.resolve(candidate.id, status="dismissed")
    with pytest.raises(ValueError, match="already dismissed"):
        store.update(candidate.id, statement="Too late")
