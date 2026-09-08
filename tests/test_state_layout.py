# SPDX-License-Identifier: Apache-2.0
"""Application state-root migration and previous-image compatibility."""

from __future__ import annotations

from pathlib import Path

import pytest

from gov_webui.state_layout import (
    StateLayoutError,
    contexts_root,
    migrate_state_layout,
    shared_root,
)


def test_migration_is_write_through_for_new_and_previous_images(tmp_path: Path) -> None:
    legacy_contexts = tmp_path / ".governor"
    legacy_shared = tmp_path / "marginalia"
    (legacy_contexts / "erin" / "sessions").mkdir(parents=True)
    (legacy_contexts / "erin" / "sessions" / "before.json").write_text("old")
    legacy_shared.mkdir()
    (legacy_shared / "library.json").write_text("{}")

    result = migrate_state_layout(tmp_path)

    assert result["contexts"] == "moved"
    assert (contexts_root(tmp_path) / "erin" / "sessions" / "before.json").read_text() == "old"
    assert (shared_root(tmp_path) / "library.json").read_text() == "{}"
    assert legacy_contexts.is_symlink()
    assert legacy_shared.is_symlink()

    # New image write, previous image read.
    (contexts_root(tmp_path) / "erin" / "sessions" / "new.json").write_text("new")
    assert (legacy_contexts / "erin" / "sessions" / "new.json").read_text() == "new"
    # Previous image write, new image read.
    (legacy_shared / "previous-image.json").write_text("rollback")
    assert (shared_root(tmp_path) / "previous-image.json").read_text() == "rollback"

    assert migrate_state_layout(tmp_path)["contexts"] == "already_migrated"


def test_migration_refuses_two_independent_state_trees(tmp_path: Path) -> None:
    (tmp_path / ".governor").mkdir()
    contexts_root(tmp_path).mkdir(parents=True)
    with pytest.raises(StateLayoutError, match="both legacy"):
        migrate_state_layout(tmp_path)
