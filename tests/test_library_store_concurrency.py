# SPDX-License-Identifier: Apache-2.0
"""The library is rewritten wholesale, so an external edit must not be clobbered.

`LibraryStore` holds the entire library in memory and writes the whole file on
every change. Before this guard, editing `library.json` outside the running
application silently lost whatever the edit added — conversation registrations
most often — because the next in-application change overwrote it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gov_webui.library_store import LibraryConcurrentModificationError, LibraryStore


def store_at(tmp_path: Path) -> LibraryStore:
    return LibraryStore(tmp_path / "library.json", default_context_id="erin-novel")


def test_an_external_edit_is_refused_rather_than_overwritten(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    project = store.snapshot().default_project_id

    # Somebody edits the file outside the application.
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["conversations"]["hand-added"] = {
        "session_id": "hand-added",
        "project_id": project,
        "title": "Added by hand",
        "created_at": "2026-09-06T00:00:00+00:00",
        "updated_at": "2026-09-06T00:00:00+00:00",
    }
    store.path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(LibraryConcurrentModificationError, match="outside the application"):
        store.create_project("Next project")

    # The failed operation also rolls back in-memory state; readers must not
    # observe a project that was never committed.
    assert all(project.name != "Next project" for project in store.list_projects())

    # The hand edit survives: refusing is the only safe answer, because the
    # application cannot merge a change it never saw.
    after = json.loads(store.path.read_text(encoding="utf-8"))
    assert "hand-added" in after["conversations"]


def test_ordinary_writes_still_succeed(tmp_path: Path) -> None:
    """The guard must not make the store unusable in the normal case."""
    store = store_at(tmp_path)

    first = store.create_project("One")
    second = store.create_project("Two")

    titles = {project.name for project in store.list_projects()}
    assert {"One", "Two"} <= titles
    assert first.id != second.id


def test_a_fresh_library_writes_without_a_prior_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "library.json"
    store = LibraryStore(path, default_context_id="erin-novel")
    assert path.exists()
    assert store.snapshot().default_project_id


def test_an_unreadable_file_is_treated_as_changed(tmp_path: Path) -> None:
    """If the current state cannot be verified, do not overwrite it."""
    store = store_at(tmp_path)
    store.path.unlink()

    with pytest.raises(LibraryConcurrentModificationError):
        store.create_project("After deletion")
