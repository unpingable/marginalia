# SPDX-License-Identifier: Apache-2.0
"""M1 product-surface and creative-project application regressions."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from support import fake_governed_chat


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def product_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import gov_webui.adapter as adapter

    monkeypatch.setattr(adapter, "MARGINALIA_ENABLE_DONOR_ROUTES", False)
    monkeypatch.setattr(adapter, "GOVERNOR_CONTEXTS_DIR", str(tmp_path / "contexts"))
    monkeypatch.setattr(adapter, "GOVERNOR_CONTEXT_ID", "erin-novel")
    monkeypatch.setattr(adapter, "GOVERNOR_MODE", "fiction")
    monkeypatch.setattr(adapter, "GOVERNOR_AUTH_TOKEN", "")
    adapter._bridge = None
    adapter._context_manager = None
    adapter._session_store = None
    adapter._creative_project_store = None
    adapter._artifact_store = None
    adapter._governed_chat_adapter = fake_governed_chat(
        content="The governed project response.",
        model="fiction-model",
    )
    adapter._pending_captures.clear()

    client = TestClient(adapter.app)
    yield client, adapter

    adapter._context_manager = None
    adapter._session_store = None
    adapter._creative_project_store = None
    adapter._artifact_store = None
    adapter._governed_chat_adapter = None
    adapter._pending_captures.clear()


def test_root_is_an_intentional_marginalia_writing_shell(product_client) -> None:
    client, _ = product_client
    response = client.get("/")

    assert response.status_code == 200
    assert "Marginalia" in response.text
    assert 'id="project-settings"' in response.text
    assert 'id="chat-panel"' in response.text
    assert 'id="artifact-editor"' in response.text
    for donor_term in (
        "Phosphor",
        "Desk",
        "Intent Compiler",
        "Maker",
        "Builder",
        'id="governor-panel"',
        'id="model-select"',
    ):
        assert donor_term not in response.text


def test_codex_provider_wrapper_delegates_default_model_to_cli(tmp_path: Path) -> None:
    native = tmp_path / "codex-native"
    native.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    native.chmod(0o755)
    environment = os.environ.copy()
    environment["CODEX_NATIVE_PATH"] = str(native)

    result = subprocess.run(
        [
            str(REPO_ROOT / "codex-provider.sh"),
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-m",
            "codex-default",
            "-",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.splitlines() == [
        "exec",
        "--json",
        "--skip-git-repo-check",
        "-",
    ]


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/governor/ui",
        "/governor/status",
        "/governor/code/project",
        "/governor/research/state",
        "/governor/receipts/export",
        "/governor/config/effective",
        "/v2/runs",
        "/v2/intent/templates",
        "/docs",
        "/openapi.json",
    ],
)
def test_donor_operator_routes_are_unreachable_by_default(
    product_client, path: str
) -> None:
    client, _ = product_client
    assert client.get(path).status_code == 404


def test_product_api_info_lists_only_writing_surfaces(product_client) -> None:
    client, _ = product_client
    endpoints = client.get("/api/info").json()["endpoints"]

    assert endpoints["project"] == "/v1/project"
    assert endpoints["markdown"] == "/v1/markdown"
    assert endpoints["fiction_characters"] == "/governor/fiction/characters"
    assert endpoints["artifacts"] == "/governor/artifacts"
    assert not any(key.startswith("v2_") for key in endpoints)
    assert "code_decisions" not in endpoints
    assert "research_state" not in endpoints
    assert "governor_ui" not in endpoints


def test_conversation_surface_renders_assistant_markdown(product_client) -> None:
    client, _ = product_client
    response = client.post(
        "/v1/markdown",
        json={"content": "## Shuffle All\n\nIt is *almost* inevitable."},
    )

    assert response.status_code == 200
    assert response.json()["html"] == (
        "<h2>Shuffle All</h2>\n"
        "<p>It is <em>almost</em> inevitable.</p>\n"
    )

    shell = client.get("/").text
    assert 'api("/v1/markdown"' in shell
    assert "renderAssistantMarkdown(body, content)" in shell


def test_runtime_entrypoint_refuses_nonfiction_mode(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment.pop("MARGINALIA_ENABLE_DONOR_ROUTES", None)
    environment.update({
        "GOVERNOR_MODE": "research",
        "MARGINALIA_DATA_ROOT": str(tmp_path / "data"),
    })

    result = subprocess.run(
        [str(REPO_ROOT / "entrypoint.sh")],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 1
    assert "fiction-only" in result.stderr
    assert not (tmp_path / "data").exists()


def test_project_settings_persist_and_reach_every_governed_fiction_request(
    product_client,
) -> None:
    client, adapter = product_client
    initial = client.get("/v1/project").json()
    assert initial["context_id"] == "erin-novel"
    assert initial["has_guidance"] is False

    saved = client.put(
        "/v1/project",
        json={
            "project_brief": "A haunted-house novel told through repairs.",
            "collaborator_stance": "Be a questioning developmental collaborator.",
            "voice_style_guidance": "Tactile, patient, and unsentimental.",
            "expected_version": initial["version"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["has_guidance"] is True

    # Reconstruct the store as a process restart would.
    adapter._creative_project_store = None
    assert client.get("/v1/project").json()["project_brief"].startswith("A haunted")

    for user_text in ("Open on the kitchen wall.", "Continue into the night."):
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": user_text}]},
        )
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == (
            "The governed project response."
        )

    calls = adapter._governed_chat_adapter.chat_send.call_args_list
    assert len(calls) == 2
    for call in calls:
        messages = call.kwargs["messages"]
        project_messages = [
            message for message in messages
            if "MARGINALIA_PROJECT_CONTEXT_V1" in message["content"]
        ]
        assert len(project_messages) == 1
        prompt = project_messages[0]["content"]
        assert "haunted-house" in prompt
        assert "questioning developmental" in prompt
        assert "Tactile, patient" in prompt


def test_project_b_cannot_receive_project_a_prompt_context(product_client) -> None:
    client, adapter = product_client
    client.put(
        "/v1/project",
        json={
            "project_brief": "PROJECT_A_SECRET",
            "collaborator_stance": "A stance",
            "voice_style_guidance": "A voice",
        },
    )

    adapter.GOVERNOR_CONTEXT_ID = "second-novel"
    adapter._creative_project_store = None
    adapter._session_store = None
    # Use a fresh complete fake after changing only the application context.
    adapter._governed_chat_adapter = fake_governed_chat(
        content="Project B response", model="fiction-model"
    )

    project_b = client.get("/v1/project").json()
    assert project_b["context_id"] == "second-novel"
    assert project_b["has_guidance"] is False
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Begin B."}]},
    )
    assert response.status_code == 200
    messages = adapter._governed_chat_adapter.chat_send.call_args.kwargs["messages"]
    assert messages == [{"role": "user", "content": "Begin B."}]
    assert "PROJECT_A_SECRET" not in str(messages)


def test_writer_export_contains_project_bible_conversations_and_drafts(
    product_client,
) -> None:
    client, _ = product_client
    client.put(
        "/v1/project",
        json={
            "project_brief": "A compact mystery.",
            "collaborator_stance": "Continuity editor",
            "voice_style_guidance": "Clear and tense",
        },
    )
    client.post(
        "/governor/fiction/characters",
        json={"name": "Inez", "description": "A conservator", "voice": "Dry"},
    )
    session = client.post("/sessions/", json={"title": "Opening"}).json()
    client.post(
        f"/sessions/{session['id']}/messages",
        json={"role": "user", "content": "Begin at dusk."},
    )
    client.post(
        "/governor/artifacts",
        json={"title": "Opening", "content": "The bell stopped.", "kind": "markdown"},
    )

    exported = client.get("/v1/project/export")

    assert exported.status_code == 200
    payload = exported.json()
    assert payload["schema"] == "marginalia.creative-project-export/v1"
    assert payload["project"]["project_brief"] == "A compact mystery."
    assert payload["story_bible"]["characters"][0]["name"] == "Inez"
    assert payload["conversations"][0]["messages"][0]["content"] == "Begin at dusk."
    assert payload["artifacts"][0]["revisions"][0]["content"] == "The bell stopped."
    assert "receipt" not in str(payload).lower()
