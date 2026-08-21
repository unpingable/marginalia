# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from gov_webui.manuscript_store import ManuscriptStore


def test_manuscript_hierarchy_order_and_artifact_reference_survive_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manuscript.json"
    store = ManuscriptStore(path)
    part = store.create(kind="part", title="Part One")
    chapter = store.create(kind="chapter", title="The Arrival", parent_id=part.id)
    first = store.create(
        kind="scene",
        title="At the station",
        parent_id=chapter.id,
        artifact_id="artifact-1",
        status="drafting",
    )
    second = store.create(
        kind="scene",
        title="The locked room",
        parent_id=chapter.id,
        artifact_id="artifact-2",
    )

    restored = ManuscriptStore(path)
    nodes, version = restored.list_nodes()
    assert version == 5
    assert {node.id for node in nodes} == {part.id, chapter.id, first.id, second.id}
    assert restored.get(first.id).artifact_id == "artifact-1"
    assert restored.get(first.id).status == "drafting"


def test_move_reorders_siblings_and_rejects_cycles(tmp_path: Path) -> None:
    store = ManuscriptStore(tmp_path / "manuscript.json")
    part = store.create(kind="part", title="Part")
    chapter = store.create(kind="chapter", title="Chapter", parent_id=part.id)
    first = store.create(kind="scene", title="First", parent_id=chapter.id)
    second = store.create(kind="scene", title="Second", parent_id=chapter.id)

    store.move(second.id, parent_id=chapter.id, position=0)
    assert store.get(second.id).position == 0
    assert store.get(first.id).position == 1

    with pytest.raises(ValueError, match="descendant"):
        store.move(part.id, parent_id=chapter.id, position=0)


def test_update_unlinks_artifact_explicitly(tmp_path: Path) -> None:
    store = ManuscriptStore(tmp_path / "manuscript.json")
    scene = store.create(kind="scene", title="Scene", artifact_id="artifact-1")
    updated = store.update(
        scene.id,
        title="Revised scene",
        artifact_id=None,
        set_artifact=True,
        status="revised",
    )
    assert updated.title == "Revised scene"
    assert updated.artifact_id is None
    assert updated.status == "revised"


def test_delete_requires_children_to_be_handled_first(tmp_path: Path) -> None:
    store = ManuscriptStore(tmp_path / "manuscript.json")
    chapter = store.create(kind="chapter", title="Chapter")
    scene = store.create(kind="scene", title="Scene", parent_id=chapter.id)
    with pytest.raises(ValueError, match="child nodes"):
        store.delete(chapter.id)
    assert store.delete(scene.id) is True
    assert store.delete(chapter.id) is True
