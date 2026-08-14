# SPDX-License-Identifier: Apache-2.0
"""Creative-project persistence, isolation, and prompt rendering."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gov_webui.creative_project import (
    CreativeProjectContextMismatch,
    CreativeProjectStore,
    CreativeProjectVersionConflict,
    render_project_context,
)


def test_project_configuration_survives_store_restart(tmp_path: Path) -> None:
    context_root = tmp_path / "contexts" / "erin-novel"
    first = CreativeProjectStore(context_root, "erin-novel")
    saved = first.update(
        project_brief="A winter-set epistolary ghost story.",
        collaborator_stance="Act as a continuity-conscious developmental collaborator.",
        voice_style_guidance="Restrained, tactile prose with dry humor.",
        expected_version=1,
    )

    restarted = CreativeProjectStore(context_root, "erin-novel").get()

    assert restarted == saved
    assert restarted.version == 2
    assert (context_root / "marginalia" / "project.json").is_file()


def test_projects_are_isolated_by_context_root_and_identity(tmp_path: Path) -> None:
    project_a = CreativeProjectStore(tmp_path / "a", "project-a")
    project_b = CreativeProjectStore(tmp_path / "b", "project-b")
    project_a.update(
        project_brief="Project A: a lunar mystery.",
        collaborator_stance="Co-writer",
        voice_style_guidance="Spare",
    )
    project_b.update(
        project_brief="Project B: a comic village novel.",
        collaborator_stance="Critical editor",
        voice_style_guidance="Expansive",
    )

    assert "lunar" in project_a.get().project_brief
    assert "village" in project_b.get().project_brief
    assert project_a.path != project_b.path


def test_copied_project_file_fails_closed_in_another_context(tmp_path: Path) -> None:
    project_a = CreativeProjectStore(tmp_path / "a", "project-a")
    project_a.update(
        project_brief="A-only direction",
        collaborator_stance="",
        voice_style_guidance="",
    )
    destination = tmp_path / "b" / "marginalia"
    destination.mkdir(parents=True)
    shutil.copyfile(project_a.path, destination / "project.json")

    with pytest.raises(CreativeProjectContextMismatch, match="project-a"):
        CreativeProjectStore(tmp_path / "b", "project-b")


def test_project_update_rejects_stale_version(tmp_path: Path) -> None:
    store = CreativeProjectStore(tmp_path / "context", "novel")
    store.update(
        project_brief="First",
        collaborator_stance="",
        voice_style_guidance="",
        expected_version=1,
    )

    with pytest.raises(CreativeProjectVersionConflict, match="current version is 2"):
        store.update(
            project_brief="Stale",
            collaborator_stance="",
            voice_style_guidance="",
            expected_version=1,
        )


def test_prompt_block_contains_only_the_selected_projects_guidance(tmp_path: Path) -> None:
    store = CreativeProjectStore(tmp_path / "project", "project-a")
    config = store.update(
        project_brief="The Glass Orchard",
        collaborator_stance="Challenge convenient plot turns.",
        voice_style_guidance="Lyrical but unsentimental.",
    )

    message = render_project_context(config)

    assert message is not None
    assert message["role"] == "system"
    assert "[MARGINALIA_PROJECT_CONTEXT_V1]" in message["content"]
    encoded = message["content"].split("\n", 2)[2].rsplit("\n", 1)[0]
    payload = json.loads(encoded)
    assert payload == {
        "project_brief": "The Glass Orchard",
        "collaborator_stance": "Challenge convenient plot turns.",
        "voice_style_guidance": "Lyrical but unsentimental.",
    }
    assert "project-a" not in message["content"]


def test_empty_project_adds_no_system_message(tmp_path: Path) -> None:
    config = CreativeProjectStore(tmp_path / "project", "empty").get()
    assert render_project_context(config) is None
