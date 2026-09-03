# SPDX-License-Identifier: Apache-2.0
"""M1 product-surface and creative-project application regressions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from support import fake_governed_chat
from gov_webui.context_summary import (
    ContextPolicy,
    ContextSummary,
    SummaryFact,
    SummaryGenerator,
    SummarySections,
    source_for,
    utc_now,
)
from gov_webui.session_store import SessionMessage


REPO_ROOT = Path(__file__).resolve().parents[1]


def _wrapper_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        [str(Path(sys.executable).parent), environment.get("PATH", "")]
    )
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            [str(REPO_ROOT / "src"), environment.get("PYTHONPATH", "")],
        )
    )
    return environment


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
    adapter._library_store = None
    adapter._session_stores.clear()
    adapter._governed_chat_adapters.clear()
    adapter._creative_project_stores.clear()
    adapter._artifact_stores.clear()
    adapter._canon_review_stores.clear()
    adapter._manuscript_stores.clear()
    adapter._snapshot_stores.clear()
    adapter._context_summary_stores.clear()
    adapter._context_maintenance_adapters.clear()
    adapter._context_maintenance_tasks.clear()
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
    adapter._library_store = None
    adapter._session_stores.clear()
    adapter._governed_chat_adapters.clear()
    adapter._creative_project_stores.clear()
    adapter._artifact_stores.clear()
    adapter._canon_review_stores.clear()
    adapter._manuscript_stores.clear()
    adapter._snapshot_stores.clear()
    adapter._context_summary_stores.clear()
    adapter._context_maintenance_adapters.clear()
    adapter._context_maintenance_tasks.clear()
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
    assert 'id="model-select"' in response.text
    for donor_term in (
        "Phosphor",
        "Desk",
        "Intent Compiler",
        "Maker",
        "Builder",
        'id="governor-panel"',
    ):
        assert donor_term not in response.text


def test_codex_provider_wrapper_delegates_default_model_to_cli(tmp_path: Path) -> None:
    native = tmp_path / "codex-native"
    native.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    native.chmod(0o755)
    environment = _wrapper_environment()
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


def test_codex_provider_wrapper_times_out_and_reaps_stalled_cli(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "native.pid"
    native = tmp_path / "codex-native"
    native.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import time\n"
        "from pathlib import Path\n"
        "Path(os.environ['NATIVE_PID_FILE']).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    native.chmod(0o755)
    environment = _wrapper_environment()
    environment.update(
        {
            "CODEX_NATIVE_PATH": str(native),
            "MARGINALIA_CODEX_TIMEOUT_SECONDS": "0.1",
            "NATIVE_PID_FILE": str(pid_file),
        }
    )

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
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Codex response timed out after 0.1 seconds\n"
    native_pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(native_pid, 0)


@pytest.mark.parametrize("value", ["0", "1801", "nan", "not-a-number"])
def test_codex_provider_wrapper_refuses_invalid_timeout(tmp_path: Path, value: str) -> None:
    native = tmp_path / "codex-native"
    native.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    native.chmod(0o755)
    environment = _wrapper_environment()
    environment.update(
        {
            "CODEX_NATIVE_PATH": str(native),
            "MARGINALIA_CODEX_TIMEOUT_SECONDS": value,
        }
    )

    result = subprocess.run(
        [str(REPO_ROOT / "codex-provider.sh"), "exec", "--json", "-"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert "MARGINALIA_CODEX_TIMEOUT_SECONDS must be" in result.stderr


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
def test_donor_operator_routes_are_unreachable_by_default(product_client, path: str) -> None:
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
        "<h2>Shuffle All</h2>\n<p>It is <em>almost</em> inevitable.</p>\n"
    )

    shell = client.get("/").text
    assert 'api("/v1/markdown"' in shell
    assert "renderAssistantMarkdown(body, content)" in shell


def test_runtime_entrypoint_refuses_nonfiction_mode(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment.pop("MARGINALIA_ENABLE_DONOR_ROUTES", None)
    environment.update(
        {
            "GOVERNOR_MODE": "research",
            "MARGINALIA_DATA_ROOT": str(tmp_path / "data"),
        }
    )

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


def test_runtime_entrypoint_rejects_relative_provider_workdir(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment.pop("MARGINALIA_ENABLE_DONOR_ROUTES", None)
    environment.update(
        {
            "GOVERNOR_MODE": "fiction",
            "MARGINALIA_DATA_ROOT": str(tmp_path / "data"),
            "BACKEND_TYPE": "codex",
            "CODEX_PATH": "/app/codex-provider.sh",
            "CLAUDE_COMMAND_WORKDIR": "relative/provider-work",
        }
    )

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
    assert "CLAUDE_COMMAND_WORKDIR must be an absolute path" in result.stderr


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
            message for message in messages if "MARGINALIA_PROJECT_CONTEXT_V1" in message["content"]
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

    project_b_record = client.post("/v1/projects", json={"name": "Second novel"}).json()
    # Use a fresh complete fake for the second project's governed context.
    project_b_chat = fake_governed_chat(content="Project B response", model="fiction-model")
    adapter._governed_chat_adapters[project_b_record["context_id"]] = project_b_chat

    project_b = client.get("/v1/project", params={"project_id": project_b_record["id"]}).json()
    assert project_b["context_id"] == project_b_record["context_id"]
    assert project_b["has_guidance"] is False
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "Begin B."}],
            "project_id": project_b_record["id"],
        },
    )
    assert response.status_code == 200
    messages = project_b_chat.chat_send.call_args.kwargs["messages"]
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


class ProductWordCounter:
    def count_text(self, text):
        return len(text.split())

    def count_messages(self, messages):
        return sum(len(item["content"].split()) + 1 for item in messages)


def _seed_long_product_session(client, adapter):
    created = client.post(
        "/sessions/",
        json={"title": "Long story", "model": "fiction-model", "project_id": "default"},
    ).json()
    store = adapter._get_session_store("default")
    messages = []
    for index in range(3):
        messages.extend(
            [
                SessionMessage.create("user", " ".join([f"user{index}"] * 900)),
                SessionMessage.create("assistant", " ".join([f"passage{index}"] * 900)),
            ]
        )
    assert store.append_messages(created["id"], messages)
    return store.get(created["id"])


def _enable_test_budget(adapter, monkeypatch):
    context_store = adapter._get_context_summary_store("default")
    context_store.save_policy(
        ContextPolicy(
            enabled=True,
            target_provider_input_tokens=8_000,
            provider_overhead_tokens=4_000,
            output_reserve_tokens=1_000,
            summary_max_tokens=1_000,
            summary_chunk_tokens=2_000,
            updated_at=utc_now(),
        )
    )
    monkeypatch.setattr(
        adapter,
        "TiktokenCounter",
        lambda *args, **kwargs: ProductWordCounter(),
    )
    return context_store


def test_context_maintenance_failure_preserves_story_and_never_reenters_context(
    product_client,
    monkeypatch,
) -> None:
    client, adapter = product_client
    session = _seed_long_product_session(client, adapter)
    context_store = _enable_test_budget(adapter, monkeypatch)
    scheduled = []
    monkeypatch.setattr(
        adapter,
        "_schedule_context_maintenance",
        lambda **kwargs: scheduled.append(kwargs),
    )
    before = session.to_dict()
    prompt = "Continue without forgetting the station."

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "fiction-model",
            "project_id": "default",
            "session_id": session.id,
            "messages": [
                *[{"role": item.role, "content": item.content} for item in session.messages],
                {"role": "user", "content": prompt},
            ],
        },
    )

    assert response.status_code == 503
    assert response.json()["outcome"] == "failure"
    assert response.json()["failure_type"] == "context_maintenance"
    assert adapter._get_session_store("default").get(session.id).to_dict() == before
    assert adapter._governed_chat_adapter.chat_send.await_count == 0
    assert scheduled == []

    # A later successful turn receives durable story only, never the operational message.
    context_store.set_enabled(False)
    retry = client.post(
        "/v1/chat/completions",
        json={
            "model": "fiction-model",
            "project_id": "default",
            "session_id": session.id,
            "messages": [
                *[{"role": item.role, "content": item.content} for item in session.messages],
                {"role": "user", "content": prompt},
            ],
        },
    )
    assert retry.status_code == 200
    forwarded = adapter._governed_chat_adapter.chat_send.await_args.kwargs["messages"]
    assert "Preparing the story context ran into trouble" not in str(forwarded)
    durable = adapter._get_session_store("default").get(session.id)
    assert len(durable.messages) == len(session.messages) + 2


def test_valid_summary_bounds_provider_context_and_success_commits_once(
    product_client,
    monkeypatch,
) -> None:
    client, adapter = product_client
    session = _seed_long_product_session(client, adapter)
    context_store = _enable_test_budget(adapter, monkeypatch)
    scheduled = []
    monkeypatch.setattr(
        adapter,
        "_schedule_context_maintenance",
        lambda **kwargs: scheduled.append(kwargs),
    )
    prefix = session.messages[:4]
    summary = ContextSummary(
        source=source_for(session, prefix),
        generator=SummaryGenerator(
            configured_model="claude-sonnet-4-20250514",
            provider_id="claude-code-local",
            model_id="sonnet",
            receipt_ids=["summary-receipt"],
        ),
        created_at=utc_now(),
        sections=SummarySections(
            narrative_recap=[
                SummaryFact(
                    text="Earlier events are represented as derived context.",
                    evidence_message_ids=[prefix[0].id],
                )
            ]
        ),
    )
    context_store.save(summary)
    prompt = "Write the next beat."
    before_count = len(session.messages)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "fiction-model",
            "project_id": "default",
            "session_id": session.id,
            "messages": [
                *[{"role": item.role, "content": item.content} for item in session.messages],
                {"role": "user", "content": prompt},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "authored"
    assert scheduled == [
        {
            "project_id": "default",
            "session_id": session.id,
            "writing_model": None,
        }
    ]
    forwarded = adapter._governed_chat_adapter.chat_send.await_args.kwargs["messages"]
    assert "[MARGINALIA_DERIVED_CONTEXT_V1]" in str(forwarded)
    assert prefix[0].content not in str(forwarded)
    assert session.messages[4].content in str(forwarded)
    durable = adapter._get_session_store("default").get(session.id)
    assert len(durable.messages) == before_count + 2
    assert [item.content for item in durable.messages[-2:]] == [
        prompt,
        "The governed project response.",
    ]
