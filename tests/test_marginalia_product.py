# SPDX-License-Identifier: Apache-2.0
"""M1 product-surface and creative-project application regressions."""

from __future__ import annotations

import asyncio
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
from gov_webui.evidence_store import EncryptedEvidenceStore, create_keyring
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
    adapter._generation_stores.clear()
    adapter._context_maintenance_adapters.clear()
    adapter._context_maintenance_tasks.clear()
    adapter._context_maintenance_pending.clear()
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
    adapter._generation_stores.clear()
    adapter._context_maintenance_adapters.clear()
    adapter._context_maintenance_tasks.clear()
    adapter._context_maintenance_pending.clear()
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
    assert "Category remains open" in response.text
    assert 'id="rule-closes-category-toggle"' in response.text
    assert "This rule lists every member of a category" in response.text
    for donor_term in (
        "Phosphor",
        "Desk",
        "Intent Compiler",
        "Maker",
        "Builder",
        'id="governor-panel"',
    ):
        assert donor_term not in response.text


def test_cross_origin_access_requires_an_explicit_origin(monkeypatch) -> None:
    import gov_webui.adapter as adapter

    monkeypatch.setenv(
        "MARGINALIA_ALLOWED_ORIGINS",
        "https://writing.example, http://192.168.69.30:4000/",
    )
    assert adapter._allowed_origins() == [
        "https://writing.example",
        "http://192.168.69.30:4000",
    ]
    monkeypatch.setenv("MARGINALIA_ALLOWED_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="wildcards"):
        adapter._allowed_origins()


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


def test_durable_generation_toggle_is_prominent_and_guarded(product_client, monkeypatch) -> None:
    client, adapter = product_client

    unavailable = client.get("/v1/generation/settings")
    assert unavailable.status_code == 200
    assert unavailable.json()["available"] is False
    refused = client.put("/v1/generation/settings", json={"enabled": True})
    assert refused.status_code == 503

    monkeypatch.setattr(adapter, "MARGINALIA_DURABLE_GENERATION_AVAILABLE", True)
    enabled = client.put(
        "/v1/generation/settings",
        json={"enabled": True, "fallback_model": "fallback-model"},
    )
    assert enabled.status_code == 200
    assert enabled.json() == {
        "available": True,
        "enabled": True,
        "fallback_model": "fallback-model",
        "status": "ready",
    }

    page = client.get("/").text
    assert "Generation reliability" in page
    assert 'id="durable-generation"' in page
    assert "stops new durable dispatches" in page


def test_durable_chat_is_idempotent_and_does_not_dispatch_synchronously(
    product_client, monkeypatch
) -> None:
    client, adapter = product_client
    monkeypatch.setattr(adapter, "MARGINALIA_DURABLE_GENERATION_AVAILABLE", True)
    assert client.put("/v1/generation/settings", json={"enabled": True}).status_code == 200
    session = client.post(
        "/sessions/",
        json={"title": "Durable", "model": "fiction-model", "project_id": "default"},
    ).json()
    request = {
        "model": "fiction-model",
        "project_id": "default",
        "session_id": session["id"],
        "client_request_id": "browser-request-1",
        "messages": [{"role": "user", "content": "Continue safely."}],
    }

    first = client.post("/v1/chat/completions", json=request)
    repeated = client.post("/v1/chat/completions", json=request)
    assert first.status_code == repeated.status_code == 202
    assert first.json()["request_id"] == repeated.json()["request_id"]
    assert adapter._governed_chat_adapter.chat_send.await_count == 0
    logical = adapter._get_generation_store("default").get_request(first.json()["request_id"])
    assert logical is not None
    assert len(adapter._get_generation_store("default").list_dispatches(logical.id)) == 1

    conflicting = client.post(
        "/v1/chat/completions",
        json={
            **request,
            "messages": [{"role": "user", "content": "Different frozen work."}],
        },
    )
    assert conflicting.status_code == 409
    assert "different frozen work" in conflicting.json()["detail"]


def _record_durable_candidate(adapter, request_id: str, keyring: Path, content: str) -> None:
    store = adapter._get_generation_store("default")
    logical = store.get_request(request_id)
    assert logical is not None
    dispatch = store.list_dispatches(request_id)[-1]
    store.mark_executing(dispatch.id, f"provider-{request_id}")
    project = adapter._project_record("default")
    context = adapter._get_context_manager().get_or_create(project.context_id, mode="fiction")
    evidence = EncryptedEvidenceStore(context.root / "marginalia" / "generation-evidence", keyring)
    reference = evidence.write(
        {
            "content": content,
            "model": dispatch.actual_model,
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            "receipt": {"receipt_id": f"receipt-{request_id}"},
        },
        logical_request_id=request_id,
        dispatch_id=dispatch.id,
        docket_attempt=f"attempt-{request_id}",
    )
    store.record_candidate(
        dispatch.id,
        response_digest=reference.response_digest,
        evidence_ref=reference.reference,
    )


def test_lost_ack_replay_returns_historical_acceptance_even_after_switch_off(
    product_client, monkeypatch, tmp_path: Path
) -> None:
    client, adapter = product_client
    monkeypatch.setattr(adapter, "MARGINALIA_DURABLE_GENERATION_AVAILABLE", True)
    keyring = tmp_path / "secrets" / "evidence.json"
    create_keyring(keyring, key_id="test", key=b"k" * 32)
    monkeypatch.setattr(adapter, "MARGINALIA_EVIDENCE_KEY_FILE", str(keyring))
    assert client.put("/v1/generation/settings", json={"enabled": True}).status_code == 200
    session = client.post(
        "/sessions/",
        json={"title": "Lost acknowledgement", "model": "fiction-model"},
    ).json()
    request = {
        "model": "fiction-model",
        "project_id": "default",
        "session_id": session["id"],
        "client_request_id": "lost-ack-1",
        "messages": [{"role": "user", "content": "Continue exactly once."}],
    }
    pending = client.post("/v1/chat/completions", json=request)
    _record_durable_candidate(adapter, pending.json()["request_id"], keyring, "One result.")

    accepted = client.get(f"/v1/generations/{pending.json()['request_id']}")
    assert accepted.status_code == 200
    assert accepted.json()["outcome"] == "authored"
    assert accepted.json()["client_request_id"] == "lost-ack-1"

    assert client.put("/v1/generation/settings", json={"enabled": False}).status_code == 200
    replay = client.post("/v1/chat/completions", json=request)
    assert replay.status_code == 200
    assert replay.json()["committed_messages"] == accepted.json()["committed_messages"]
    durable = client.get(f"/sessions/{session['id']}").json()
    assert [item["content"] for item in durable["messages"]] == [
        "Continue exactly once.",
        "One result.",
    ]

    listing = client.get(
        "/v1/generations",
        params={"session_id": session["id"], "client_request_id": "lost-ack-1"},
    ).json()
    assert listing["generations"][0]["outcome"] == "authored"

    new_delivery = client.post(
        "/v1/chat/completions",
        json={
            **request,
            "client_request_id": "new-while-off",
            "messages": [
                {"role": item["role"], "content": item["content"]} for item in durable["messages"]
            ]
            + [{"role": "user", "content": "Do not dispatch."}],
        },
    )
    assert new_delivery.status_code == 409
    assert "not rerouted" in new_delivery.json()["detail"]
    assert adapter._governed_chat_adapter.chat_send.await_count == 0


def test_two_tabs_from_one_revision_accept_exactly_one_durable_candidate(
    product_client, monkeypatch, tmp_path: Path
) -> None:
    client, adapter = product_client
    monkeypatch.setattr(adapter, "MARGINALIA_DURABLE_GENERATION_AVAILABLE", True)
    keyring = tmp_path / "secrets" / "evidence.json"
    create_keyring(keyring, key_id="test", key=b"k" * 32)
    monkeypatch.setattr(adapter, "MARGINALIA_EVIDENCE_KEY_FILE", str(keyring))
    client.put("/v1/generation/settings", json={"enabled": True})
    session = client.post("/sessions/", json={"title": "Two tabs", "model": "fiction-model"}).json()
    base = {
        "model": "fiction-model",
        "project_id": "default",
        "session_id": session["id"],
    }
    first = client.post(
        "/v1/chat/completions",
        json={**base, "client_request_id": "tab-a", "messages": [{"role": "user", "content": "A"}]},
    ).json()
    second = client.post(
        "/v1/chat/completions",
        json={**base, "client_request_id": "tab-b", "messages": [{"role": "user", "content": "B"}]},
    ).json()
    _record_durable_candidate(adapter, first["request_id"], keyring, "Result A")
    _record_durable_candidate(adapter, second["request_id"], keyring, "Result B")

    assert client.get(f"/v1/generations/{first['request_id']}").json()["outcome"] == "authored"
    rejected = client.get(f"/v1/generations/{second['request_id']}").json()
    assert rejected["outcome"] == "blocked"
    assert rejected["message"] == "session revision changed"
    durable = client.get(f"/sessions/{session['id']}").json()
    assert [item["content"] for item in durable["messages"]] == ["A", "Result A"]


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
    assert scheduled == [
        {
            "project_id": "default",
            "session_id": session.id,
            "writing_model": None,
            "required_covered_messages": 6,
        }
    ]

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
    assert "The story context is being prepared" not in str(forwarded)
    durable = adapter._get_session_store("default").get(session.id)
    assert len(durable.messages) == len(session.messages) + 2


@pytest.mark.asyncio
async def test_prompt_sized_coverage_gap_does_not_wedge_maintenance_forever(
    product_client,
    monkeypatch,
) -> None:
    """A summary that satisfies the placeholder plan but not the real prompt.

    Maintenance plans its own prefix from a short placeholder prompt. When a
    writer's real prompt needs more coverage than that, maintenance used to see
    its own estimate already satisfied and return without doing anything, so
    every retry failed identically and the story could never leave maintenance.
    """
    client, adapter = product_client
    # Many small exchanges, so the required prefix moves with the prompt size.
    created = client.post(
        "/sessions/",
        json={"title": "Long story", "model": "fiction-model", "project_id": "default"},
    ).json()
    session_store = adapter._get_session_store("default")
    seeded = []
    for index in range(14):
        seeded.extend(
            [
                SessionMessage.create("user", " ".join([f"user{index}"] * 120)),
                SessionMessage.create("assistant", " ".join([f"passage{index}"] * 120)),
            ]
        )
    assert session_store.append_messages(created["id"], seeded)
    session = session_store.get(created["id"])
    context_store = _enable_test_budget(adapter, monkeypatch)

    # Cover exactly what maintenance's own placeholder plan asks for, and no more.
    from gov_webui.context_budget import choose_summary_prefix, maintenance_lookahead_tokens

    policy = context_store.policy()
    counter = ProductWordCounter()
    placeholder_prefix = choose_summary_prefix(
        session,
        adapter._fixed_context_messages("default"),
        "Continue the story.",
        policy,
        counter,
        additional_reserve_tokens=maintenance_lookahead_tokens(policy),
    )
    context_store.save(
        ContextSummary(
            source=source_for(session, placeholder_prefix),
            generator=SummaryGenerator(configured_model="claude-context-summary"),
            created_at=utc_now(),
            sections=SummarySections(
                narrative_recap=[
                    SummaryFact(
                        text="Derived context.",
                        evidence_message_ids=[placeholder_prefix[0].id],
                    )
                ]
            ),
        )
    )

    scheduled: list[dict] = []
    monkeypatch.setattr(
        adapter,
        "_schedule_context_maintenance",
        lambda **kwargs: scheduled.append(kwargs),
    )
    before = adapter._get_session_store("default").get(session.id).to_dict()

    # A prompt materially longer than the placeholder needs more covered history —
    # and large enough that the *measured* composition genuinely overflows, not
    # merely the planning estimate. Admission tries the real summary first now, so
    # a gap that only the estimate saw is admitted rather than blocked.
    prompt = " ".join(["remember"] * 1200)
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
    assert response.json()["failure_type"] == "context_maintenance"
    # The story is untouched and the provider was never launched.
    assert adapter._get_session_store("default").get(session.id).to_dict() == before
    assert adapter._governed_chat_adapter.chat_send.await_count == 0

    # Generation must report the coverage its real prompt needed.
    required = scheduled[0]["required_covered_messages"]
    assert required > len(placeholder_prefix)

    # Maintenance must act on that requirement instead of declaring itself done.
    requested: list[int] = []

    class RecordingMaintainer:
        def __init__(self, **kwargs):
            pass

        async def maintain(self, session, source_messages):
            requested.append(len(source_messages))
            raise RuntimeError("stop before contacting a provider")

    monkeypatch.setattr(adapter, "ContextMaintainer", RecordingMaintainer)
    monkeypatch.setattr(
        adapter,
        "_configured_provider_catalog",
        lambda: _MaintenanceOnlyCatalog(),
    )

    # Reproduces the wedge: planning from the placeholder alone finds its own
    # estimate already covered and silently does nothing, forever.
    await adapter._perform_context_maintenance_once(
        project_id="default",
        session_id=session.id,
        writing_model=None,
        required_covered_messages=None,
    )
    assert requested == []

    # With the coverage generation actually needed, maintenance does the work.
    with pytest.raises(RuntimeError):
        await adapter._perform_context_maintenance_once(
            project_id="default",
            session_id=session.id,
            writing_model=None,
            required_covered_messages=required,
        )

    assert requested == [required]


def test_reported_coverage_requirement_scales_with_prompt_size(
    product_client,
    monkeypatch,
) -> None:
    """The requirement is derived per prompt, never pinned to one past value.

    A longer prompt leaves less room for recent history, so generation must ask
    maintenance for strictly more coverage rather than reusing whatever number
    unwedged the previous turn.
    """
    client, adapter = product_client
    created = client.post(
        "/sessions/",
        json={"title": "Long story", "model": "fiction-model", "project_id": "default"},
    ).json()
    session_store = adapter._get_session_store("default")
    seeded = []
    for index in range(14):
        seeded.extend(
            [
                SessionMessage.create("user", " ".join([f"user{index}"] * 120)),
                SessionMessage.create("assistant", " ".join([f"passage{index}"] * 120)),
            ]
        )
    assert session_store.append_messages(created["id"], seeded)
    session = session_store.get(created["id"])
    _enable_test_budget(adapter, monkeypatch)

    scheduled: list[dict] = []
    monkeypatch.setattr(
        adapter,
        "_schedule_context_maintenance",
        lambda **kwargs: scheduled.append(kwargs),
    )

    def required_for(words: int) -> int:
        scheduled.clear()
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fiction-model",
                "project_id": "default",
                "session_id": session.id,
                "messages": [
                    *[{"role": item.role, "content": item.content} for item in session.messages],
                    {"role": "user", "content": " ".join(["remember"] * words)},
                ],
            },
        )
        assert response.status_code == 503
        assert response.json()["failure_type"] == "context_maintenance"
        return scheduled[0]["required_covered_messages"]

    assert required_for(600) > required_for(300)


@pytest.mark.asyncio
async def test_below_watermark_requirement_still_reaches_maintenance(
    product_client,
    monkeypatch,
) -> None:
    """A proactive watermark must not veto a requirement generation already proved.

    The watermark is measured on history alone; admission is measured on fixed
    project context plus history plus the prompt. A project with a substantial
    story bible can therefore need coverage while its history is still under the
    watermark. Maintenance used to return at the watermark gate before it ever
    looked at the requirement, which wedged the session permanently.
    """
    client, adapter = product_client
    created = client.post(
        "/sessions/",
        json={"title": "Bible-heavy story", "model": "fiction-model", "project_id": "default"},
    ).json()
    session_store = adapter._get_session_store("default")
    seeded = []
    for index in range(12):
        seeded.extend(
            [
                SessionMessage.create("user", " ".join([f"user{index}"] * 80)),
                SessionMessage.create("assistant", " ".join([f"passage{index}"] * 80)),
            ]
        )
    assert session_store.append_messages(created["id"], seeded)
    session = session_store.get(created["id"])
    context_store = _enable_test_budget(adapter, monkeypatch)

    # A real, large story bible — set through the product API, not monkeypatched,
    # so the fixed context under test is the one generation actually sends.
    initial = client.get("/v1/project").json()
    assert (
        client.put(
            "/v1/project",
            json={
                "project_brief": " ".join(["bible"] * 600),
                "collaborator_stance": "Be exacting.",
                "voice_style_guidance": "Tactile.",
                "expected_version": initial["version"],
            },
        ).status_code
        == 200
    )

    # Pin the Finding 1 shape: under the watermark, over the admission limit.
    from gov_webui.context_budget import maintenance_lookahead_tokens

    policy = context_store.policy()
    counter = ProductWordCounter()
    lookahead = maintenance_lookahead_tokens(policy)
    threshold = int((policy.application_tokens - lookahead) * policy.maintenance_watermark)
    prompt = " ".join(["remember"] * 300)
    fixed = adapter._fixed_context_messages("default")
    history_tokens = counter.count_messages(
        [{"role": item.role, "content": item.content} for item in session.messages]
    )
    full_tokens = counter.count_messages(
        [
            *fixed,
            *[{"role": item.role, "content": item.content} for item in session.messages],
            {"role": "user", "content": prompt},
        ]
    )
    assert history_tokens < threshold, "history must sit below the proactive watermark"
    assert full_tokens > policy.application_tokens - lookahead, "admission must still reject"

    scheduled: list[dict] = []
    monkeypatch.setattr(
        adapter,
        "_schedule_context_maintenance",
        lambda **kwargs: scheduled.append(kwargs),
    )
    before = session_store.get(session.id).to_dict()

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
    assert response.json()["failure_type"] == "context_maintenance"
    assert session_store.get(session.id).to_dict() == before
    assert adapter._governed_chat_adapter.chat_send.await_count == 0

    required = scheduled[0]["required_covered_messages"]
    assert required > 0

    requested: list[int] = []

    class RecordingMaintainer:
        def __init__(self, **kwargs):
            pass

        async def maintain(self, session, source_messages):
            requested.append(len(source_messages))
            raise RuntimeError("stop before contacting a provider")

    monkeypatch.setattr(adapter, "ContextMaintainer", RecordingMaintainer)
    monkeypatch.setattr(
        adapter,
        "_configured_provider_catalog",
        lambda: _MaintenanceOnlyCatalog(),
    )

    with pytest.raises(RuntimeError):
        await adapter._perform_context_maintenance_once(
            project_id="default",
            session_id=session.id,
            writing_model=None,
            required_covered_messages=required,
        )

    assert requested and requested[0] >= required

    # The watermark still suppresses purely proactive work on this session.
    requested.clear()
    await adapter._perform_context_maintenance_once(
        project_id="default",
        session_id=session.id,
        writing_model=None,
        required_covered_messages=None,
    )
    assert requested == []


class _MaintenanceOnlyCatalog:
    """Minimal catalog exposing one context-maintenance model."""

    def require_available(self, model_id):
        from gov_webui.model_providers import ConfiguredModel

        return ConfiguredModel(
            id=model_id,
            label="Test context maintenance",
            provider_id="test-provider",
            model_id="test-model",
            protocol="local-command",
            base_url=None,
            api_key_env=None,
            connect_timeout_seconds=1.0,
            read_timeout_seconds=1.0,
            timeout_seconds=1.0,
            purpose="context-maintenance",
        )

    def compatible_model_ids(self, model):
        return frozenset({model.id})


def test_unsummarizable_passage_fails_fast_instead_of_promising_preparation(
    product_client,
    monkeypatch,
) -> None:
    """Retry cannot chunk an oversized passage, so do not ask the writer to retry.

    A single authored message larger than the chunk budget makes maintenance
    structurally impossible. It used to burn the whole backoff schedule, fall
    silent, and leave the writer being told their context was being prepared.
    """
    client, adapter = product_client
    created = client.post(
        "/sessions/",
        json={"title": "Oversized passage", "model": "fiction-model", "project_id": "default"},
    ).json()
    session_store = adapter._get_session_store("default")
    seeded = [
        SessionMessage.create("user", " ".join(["ordinary"] * 200)),
        # One pasted passage larger than summary_chunk_tokens (2_000 words here).
        SessionMessage.create("assistant", " ".join(["enormous"] * 4_000)),
        SessionMessage.create("user", " ".join(["ordinary"] * 200)),
        SessionMessage.create("assistant", " ".join(["ordinary"] * 200)),
    ]
    assert session_store.append_messages(created["id"], seeded)
    session = session_store.get(created["id"])
    _enable_test_budget(adapter, monkeypatch)

    scheduled: list[dict] = []
    monkeypatch.setattr(
        adapter,
        "_schedule_context_maintenance",
        lambda **kwargs: scheduled.append(kwargs),
    )
    before = session_store.get(session.id).to_dict()

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "fiction-model",
            "project_id": "default",
            "session_id": session.id,
            "messages": [
                *[{"role": item.role, "content": item.content} for item in session.messages],
                {"role": "user", "content": "Continue."},
            ],
        },
    )

    body = response.json()
    assert response.status_code == 422
    assert body["failure_type"] == "context_too_large"
    assert body["retryable"] is False
    assert "being prepared" not in body["message"]
    # No pointless background work was scheduled, and the story is untouched.
    assert scheduled == []
    assert session_store.get(session.id).to_dict() == before
    assert adapter._governed_chat_adapter.chat_send.await_count == 0


@pytest.mark.asyncio
async def test_impossible_maintenance_is_not_retried_on_the_backoff_schedule(
    monkeypatch,
) -> None:
    """Terminal failures stop immediately; transient ones still retry."""
    import gov_webui.adapter as adapter
    from gov_webui.context_summary import ContextMaintenanceUnavailable, ContextTooLarge

    delays: list[float] = []

    async def no_wait(delay):
        delays.append(delay)

    monkeypatch.setattr(adapter.asyncio, "sleep", no_wait)
    monkeypatch.setattr(adapter, "CONTEXT_MAINTENANCE_RETRY_DELAYS_SECONDS", (1.0, 2.0))

    for terminal in (
        ContextTooLarge("one authored message exceeds the chunk budget"),
        ContextMaintenanceUnavailable("context maintenance requires configured model providers"),
    ):
        calls = 0

        async def fail(**kwargs):
            nonlocal calls
            calls += 1
            raise terminal

        monkeypatch.setattr(adapter, "_perform_context_maintenance_once", fail)
        delays.clear()
        await adapter._opportunistic_context_maintenance(
            project_id="default", session_id="s", writing_model=None
        )
        assert calls == 1, f"{type(terminal).__name__} must not be retried"
        assert delays == []

    # A transient failure still uses the full schedule.
    calls = 0

    async def flaky(**kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider hiccup")

    monkeypatch.setattr(adapter, "_perform_context_maintenance_once", flaky)
    delays.clear()
    await adapter._opportunistic_context_maintenance(
        project_id="default", session_id="s", writing_model=None
    )
    assert calls == 3
    assert delays == [1.0, 2.0]


@pytest.mark.asyncio
async def test_stronger_requirement_survives_weaker_maintenance_in_flight(
    monkeypatch,
) -> None:
    """Requirements join monotonically instead of being dropped.

    Maintenance takes minutes and writer retries take seconds, so a longer prompt
    almost always arrives while a weaker run holds the slot. Dropping it made the
    completed run target a stale requirement and cost the writer another turn.
    """
    import gov_webui.adapter as adapter

    adapter._context_maintenance_tasks.clear()
    adapter._context_maintenance_pending.clear()

    class EnabledStore:
        def policy(self):
            return ContextPolicy(enabled=True, updated_at=utc_now())

    monkeypatch.setattr(adapter, "_get_context_summary_store", lambda pid=None: EnabledStore())

    started: list[int | None] = []

    async def slow(**kwargs):
        started.append(kwargs.get("required_covered_messages"))
        await asyncio.sleep(0.05)

    monkeypatch.setattr(adapter, "_opportunistic_context_maintenance", slow)

    adapter._schedule_context_maintenance(
        project_id="default", session_id="s", writing_model=None, required_covered_messages=84
    )
    await asyncio.sleep(0)
    # The writer retries with longer prompts while the first run is still going.
    adapter._schedule_context_maintenance(
        project_id="default", session_id="s", writing_model=None, required_covered_messages=92
    )
    adapter._schedule_context_maintenance(
        project_id="default", session_id="s", writing_model=None, required_covered_messages=120
    )
    adapter._schedule_context_maintenance(
        project_id="default", session_id="s", writing_model=None, required_covered_messages=90
    )

    for _ in range(50):
        await asyncio.sleep(0.02)
        if not any(not task.done() for task in adapter._context_maintenance_tasks.values()):
            break
    await asyncio.sleep(0.05)

    assert started[0] == 84
    # The strongest requirement observed mid-run is satisfied by a follow-up run,
    # and the weaker 90 never displaces it.
    assert 120 in started, f"strongest requirement was lost: {started}"
    assert max(item for item in started if item is not None) == 120
    assert (
        adapter._context_maintenance_pending.get(
            adapter._context_maintenance_key("default", "s", None)
        )
        is None
    )


@pytest.mark.asyncio
async def test_distinct_writing_model_policies_get_distinct_maintenance_runs(
    monkeypatch,
) -> None:
    import gov_webui.adapter as adapter
    from gov_webui.model_providers import ConfiguredModel

    adapter._context_maintenance_tasks.clear()
    adapter._context_maintenance_pending.clear()

    class EnabledStore:
        def policy(self):
            return ContextPolicy(enabled=True, updated_at=utc_now())

    monkeypatch.setattr(adapter, "_get_context_summary_store", lambda pid=None: EnabledStore())
    started = []

    async def slow(**kwargs):
        started.append(kwargs["writing_model"].id)
        await asyncio.sleep(0.02)

    monkeypatch.setattr(adapter, "_opportunistic_context_maintenance", slow)

    def configured(model_id, window):
        return ConfiguredModel(
            id=model_id,
            label=model_id,
            provider_id="p",
            model_id=model_id,
            protocol="openai-compatible",
            base_url="http://localhost/v1",
            api_key_env=None,
            connect_timeout_seconds=1,
            read_timeout_seconds=1,
            timeout_seconds=1,
            context_window_tokens=window,
        )

    adapter._schedule_context_maintenance(
        project_id="default", session_id="s", writing_model=configured("small", 8000)
    )
    adapter._schedule_context_maintenance(
        project_id="default", session_id="s", writing_model=configured("large", 64000)
    )
    await asyncio.gather(*list(adapter._context_maintenance_tasks.values()))

    assert sorted(started) == ["large", "small"]


@pytest.mark.asyncio
async def test_context_maintenance_retries_resumable_work_off_request(
    monkeypatch,
) -> None:
    import gov_webui.adapter as adapter

    calls = 0
    delays = []

    async def fail_twice(**kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("temporary maintenance failure")

    async def no_wait(delay):
        delays.append(delay)

    monkeypatch.setattr(adapter, "_perform_context_maintenance_once", fail_twice)
    monkeypatch.setattr(adapter, "CONTEXT_MAINTENANCE_RETRY_DELAYS_SECONDS", (1.0, 2.0))
    monkeypatch.setattr(adapter.asyncio, "sleep", no_wait)

    await adapter._opportunistic_context_maintenance(
        project_id="default",
        session_id="session",
        writing_model=None,
    )

    assert calls == 3
    assert delays == [1.0, 2.0]


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
    prefix = session.messages
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
    durable = adapter._get_session_store("default").get(session.id)
    assert len(durable.messages) == before_count + 2
    assert [item.content for item in durable.messages[-2:]] == [
        prompt,
        "The governed project response.",
    ]


def test_lookahead_stale_summary_still_admits_when_the_real_context_fits(
    product_client,
    monkeypatch,
) -> None:
    """A planning estimate must not stall a turn the real context can carry.

    `choose_summary_prefix` reserves `summary_max_tokens` because it is planning
    for a summary that does not exist yet. The summary in hand is usually smaller.
    This case previously blocked the writer and told her preparation was in
    progress while maintenance produced coverage the turn never needed — which is
    what put a six-minute stall in front of a live session on 2026-09-06.

    Maintenance is still scheduled; it simply runs behind the writer instead of
    in front of her.
    """
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
    context_store.save(
        ContextSummary(
            source=source_for(session, prefix),
            generator=SummaryGenerator(configured_model="claude-sonnet-4-20250514"),
            created_at=utc_now(),
            sections=SummarySections(
                narrative_recap=[
                    SummaryFact(text="Derived context.", evidence_message_ids=[prefix[0].id])
                ]
            ),
        )
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "fiction-model",
            "project_id": "default",
            "session_id": session.id,
            "messages": [
                *[{"role": item.role, "content": item.content} for item in session.messages],
                {"role": "user", "content": "Continue."},
            ],
        },
    )

    assert response.status_code == 200
    assert adapter._governed_chat_adapter.chat_send.await_count == 1
    # Maintenance still runs, in the background, so coverage keeps moving forward.
    assert len(scheduled) == 1
    assert scheduled[0].get("required_covered_messages") is None


def test_model_context_window_binds_generation_admission(
    product_client,
    monkeypatch,
) -> None:
    """A model's declared window narrows the project budget at admission.

    Without this the project budget is applied to whichever model is selected and
    an oversized launch surfaces as an opaque provider error instead of a typed
    admission outcome.
    """
    from gov_webui.model_providers import ConfiguredModel

    client, adapter = product_client
    created = client.post(
        "/sessions/",
        json={"title": "Windowed", "model": "fiction-model", "project_id": "default"},
    ).json()
    session_store = adapter._get_session_store("default")
    seeded = []
    for index in range(6):
        seeded.append(SessionMessage.create("user", " ".join([f"u{index}"] * 200)))
        seeded.append(SessionMessage.create("assistant", " ".join([f"a{index}"] * 200)))
    assert session_store.append_messages(created["id"], seeded)
    session = session_store.get(created["id"])
    _enable_test_budget(adapter, monkeypatch)

    def model(window: int | None) -> ConfiguredModel:
        return ConfiguredModel(
            id="fiction-model",
            label="Windowed",
            provider_id="test",
            model_id="m",
            protocol="openai-compatible",
            base_url="http://localhost/v1",
            api_key_env=None,
            connect_timeout_seconds=1.0,
            read_timeout_seconds=1.0,
            timeout_seconds=1.0,
            context_window_tokens=window,
        )

    scheduled: list[dict] = []
    monkeypatch.setattr(
        adapter, "_schedule_context_maintenance", lambda **kwargs: scheduled.append(kwargs)
    )

    def send(window: int | None):
        monkeypatch.setattr(
            adapter,
            "_resolve_configured_model",
            lambda requested: ("fiction-model", model(window)),
        )
        current = session_store.get(session.id)
        return client.post(
            "/v1/chat/completions",
            json={
                "model": "fiction-model",
                "project_id": "default",
                "session_id": session.id,
                "messages": [
                    *[{"role": item.role, "content": item.content} for item in current.messages],
                    {"role": "user", "content": "Continue."},
                ],
            },
        )

    # Undeclared window: the project budget applies and the turn is authored.
    assert send(None).status_code == 200

    # The same turn on a model whose window cannot satisfy this project's budget
    # is refused before the provider is launched, as a typed outcome.
    before = session_store.get(session.id).to_dict()
    adapter._governed_chat_adapter.chat_send.reset_mock()
    response = send(5_600)
    assert response.status_code == 422
    body = response.json()
    assert body["failure_type"] == "context_too_large"
    assert body["retryable"] is False
    assert session_store.get(session.id).to_dict() == before
    assert adapter._governed_chat_adapter.chat_send.await_count == 0


def test_stateless_generation_is_refused_when_bounded_context_is_enabled(
    product_client,
    monkeypatch,
) -> None:
    """A session-less turn cannot be bounded, so name the cause the writer can act on.

    A long-lived browser tab keeps running the JavaScript it loaded with. An
    older one generated without a session id, which reached the stateless path
    and sent unbounded history. That surfaced as an opaque context error rather
    than "your tab is out of date".
    """
    client, adapter = product_client
    _enable_test_budget(adapter, monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "fiction-model",
            "project_id": "default",
            "messages": [{"role": "user", "content": "Continue the scene."}],
        },
    )

    body = response.json()
    assert response.status_code == 409
    assert body["failure_type"] == "client_outdated"
    assert body["retryable"] is False
    assert "eload" in body["message"]
    # Nothing reached the provider.
    assert adapter._governed_chat_adapter.chat_send.await_count == 0


def test_stateless_generation_still_works_without_bounded_context(
    product_client,
) -> None:
    """The guard is scoped to bounded projects; plain stateless use is unaffected."""
    client, adapter = product_client

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Open on the kitchen wall."}]},
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "authored"


# =============================================================================
# Accepted canon must accumulate, keep its subject, and read on its own
# =============================================================================


def _accept(client, adapter, *, kind: str, subject: str, statement: str):
    """Queue one capture candidate and promote it, as the review UI does."""
    project = client.get("/v1/projects").json()["projects"][0]["id"]
    item = adapter._get_canon_review_store(project).add(
        kind=kind, subject=subject, statement=statement
    )
    return client.post(
        f"/governor/fiction/capture/{item.id}/accept",
        json={"project_id": project},
    )


def test_accepting_two_facts_about_one_character_preserves_both(product_client) -> None:
    """Capture is incremental, so promotion must add rather than replace.

    The registry is keyed by anchor id and `register` overwrites, so deriving the
    id from the character name made every acceptance destroy the one before it.
    """
    client, adapter = product_client

    assert (
        _accept(
            client,
            adapter,
            kind="character",
            subject="Halo",
            statement="already established as a ghost-interpreter",
        ).status_code
        == 200
    )
    assert (
        _accept(
            client, adapter, kind="character", subject="Halo", statement="uses they/them pronouns"
        ).status_code
        == 200
    )

    characters = client.get("/governor/fiction/characters").json()["characters"]
    halo = next(c for c in characters if c["id"] == "char-halo")
    assert "ghost-interpreter" in halo["description"]
    assert "they/them" in halo["description"]


def test_repeated_acceptance_of_the_same_statement_does_not_duplicate(product_client) -> None:
    """Two candidates carrying the same statement merge to one entry."""
    client, adapter = product_client
    project = client.get("/v1/projects").json()["projects"][0]["id"]
    store = adapter._get_canon_review_store(project)

    # Queue both before either is resolved, so the store's standing-resolution
    # guard is not what is under test here.
    first = store.add(kind="character", subject="Margie", statement="a salvager in Doverton")
    second = store.add(
        kind="character", subject="Margie", statement="a salvager in Doverton ", message_id="m2"
    )
    for candidate in (first, second):
        client.post(
            f"/governor/fiction/capture/{candidate.id}/accept", json={"project_id": project}
        )

    margie = next(
        c
        for c in client.get("/governor/fiction/characters").json()["characters"]
        if c["id"] == "char-margie"
    )
    assert margie["description"].count("a salvager in Doverton") == 1


def test_promoted_constraint_keeps_its_subject(product_client) -> None:
    """Dropping the subject turns a rule about one character into a global one."""
    client, adapter = product_client

    response = _accept(
        client,
        adapter,
        kind="constraint",
        subject="Halo",
        statement="serve again, because Army policy bars former specialists",
    )
    assert response.status_code == 200

    forbidden = client.get("/governor/fiction/forbidden").json()["forbidden"]
    assert any(item["description"].startswith("Halo:") for item in forbidden)


def test_canon_statement_without_a_referent_is_refused(product_client) -> None:
    """The canon block is a flat list; a bare reference resolves against anything."""
    client, adapter = product_client

    response = _accept(
        client,
        adapter,
        kind="character",
        subject="Halo",
        statement="subject to the same prohibition for the same reason",
    )
    assert response.status_code == 422
    assert "readable as canon" in response.json()["detail"]
    assert client.get("/governor/fiction/characters").json()["characters"] == []


def test_self_contained_demonstrative_canon_is_accepted(product_client) -> None:
    client, adapter = product_client
    project = adapter._project_record(None).id
    candidate = adapter._get_canon_review_store(project).add(
        kind="world_rule",
        subject="Doverton",
        statement="This city floods every spring.",
    )

    response = client.post(
        f"/governor/fiction/capture/{candidate.id}/accept",
        json={"project_id": project},
    )

    assert response.status_code == 200


def test_accepted_character_facts_are_never_truncated(product_client) -> None:
    client, adapter = product_client
    project = adapter._project_record(None).id
    store = adapter._get_canon_review_store(project)
    statements = ["A" * 2500, "B" * 2500]
    for statement in statements:
        candidate = store.add(kind="character", subject="Halo", statement=statement)
        response = client.post(
            f"/governor/fiction/capture/{candidate.id}/accept",
            json={"project_id": project},
        )
        assert response.status_code == 200

    [halo] = client.get(f"/governor/fiction/characters?project_id={project}").json()["characters"]
    assert all(statement in halo["description"] for statement in statements)
    assert len(halo["description"]) > 4000


def test_anchor_ids_are_not_reused_after_deletion(product_client) -> None:
    """A reused id overwrites the anchor that already holds it."""
    client, adapter = product_client
    project = adapter._project_record(None).id
    # The world-rule endpoints read an existing context; the product creates one
    # the first time a project-scoped store is opened.
    adapter._get_canon_review_store(project)

    for rule in ("first rule", "second rule"):
        client.post("/governor/fiction/world-rules", json={"project_id": project, "rule": rule})
    rules = client.get("/governor/fiction/world-rules").json()["rules"]
    assert [r["id"] for r in rules] == ["world-1", "world-2"]

    # Remove world-1 out of band, as old deployments allowed. The durable
    # allocator must not reclaim the historical identity after a restart.
    from governor.continuity import create_registry

    ctx, _ = adapter._resolve_context(project)
    registry = create_registry(ctx.governor_dir)
    registry.unregister("world-1")
    registry.save(ctx.governor_dir / "continuity" / "anchors.json")
    adapter._canon_review_stores.clear()

    client.post("/governor/fiction/world-rules", json={"project_id": project, "rule": "third"})
    assert [r["id"] for r in client.get("/governor/fiction/world-rules").json()["rules"]] == [
        "world-2",
        "world-3",
    ]


def test_canon_block_separates_world_facts_from_things_not_to_write(product_client) -> None:
    """A prohibition and a world fact must not render under one heading."""
    client, adapter = product_client
    project = adapter._project_record(None).id
    adapter._get_canon_review_store(project)

    client.post(
        "/governor/fiction/world-rules",
        json={"project_id": project, "rule": "Magic requires training."},
    )
    client.post(
        "/governor/fiction/forbidden",
        json={"project_id": project, "description": "Time travel", "patterns": []},
    )

    import gov_webui.adapter as adapter

    block = adapter._build_fiction_canon_context_message(project)
    assert block is not None
    content = block["content"]
    assert '"world_facts"' in content and '"must_not_appear"' in content
    assert "Magic requires training." in content.split('"must_not_appear"')[0]
    assert "Time travel" in content.split('"must_not_appear"')[1]
    assert "Do not generalise an entry" in content


def test_ordinary_canon_containing_the_same_is_not_refused(product_client) -> None:
    """The anaphora guard must not reject legitimate description."""
    client, adapter = product_client

    response = _accept(
        client,
        adapter,
        kind="character",
        subject="Rainer",
        statement="wears the same jacket in every season",
    )
    assert response.status_code == 200


# =============================================================================
# Interpretation may be surfaced; it may not reach the registry
# =============================================================================


def test_promotion_refuses_a_candidate_that_rests_on_an_interpretation(
    product_client,
) -> None:
    """The debugger's reading of the author's rule cannot become canon."""
    client, adapter = product_client
    project = client.get("/v1/projects").json()["projects"][0]["id"]
    candidate = adapter._get_canon_review_store(project).add(
        kind="world_rule",
        subject="robots",
        statement="the rule should apply only to the robots currently named",
        warrant="scope_interpretation",
        target_anchor_id="forbid-1",
    )

    response = client.post(
        f"/governor/fiction/capture/{candidate.id}/accept", json={"project_id": project}
    )

    assert response.status_code == 422
    assert "interpretation" in response.json()["detail"]
    assert client.get("/governor/fiction/world-rules").json()["rules"] == []
    # It stays visible rather than being thrown away.
    pending = client.get(f"/governor/fiction/captures?project_id={project}").json()
    assert any(c["id"] == candidate.id for c in pending["captures"])


def test_promotion_refuses_a_candidate_leaning_on_uncanonical_propositions(
    product_client,
) -> None:
    """A mechanical warrant does not launder a model-supplied premise."""
    client, adapter = product_client
    project = client.get("/v1/projects").json()["projects"][0]["id"]
    candidate = adapter._get_canon_review_store(project).add(
        kind="world_rule",
        subject="ghost perception",
        statement="this rule contradicts itself",
        warrant="contradictory_state",
        target_anchor_id="forbid-1",
        relied_on=["world-99-having-been-human-is-sufficient"],
    )

    response = client.post(
        f"/governor/fiction/capture/{candidate.id}/accept", json={"project_id": project}
    )

    assert response.status_code == 422
    assert "not established canon" in response.json()["detail"]


def test_a_mechanically_provable_repair_still_promotes(product_client) -> None:
    """The boundary must not disarm findings that are shown against the source."""
    client, adapter = product_client
    project = adapter._project_record(None).id
    adapter._get_canon_review_store(project)
    client.post(
        "/governor/fiction/world-rules",
        json={"project_id": project, "rule": "Magic requires training."},
    )

    candidate = adapter._get_canon_review_store(project).add(
        kind="constraint",
        subject="Halo",
        statement="serve again, because Army policy bars former specialists",
        warrant="dropped_subject",
        target_anchor_id="world-1",
        relied_on=["world-1"],
    )
    response = client.post(
        f"/governor/fiction/capture/{candidate.id}/accept", json={"project_id": project}
    )

    assert response.status_code == 200
    forbidden = client.get("/governor/fiction/forbidden").json()["forbidden"]
    assert any(item["description"].startswith("Halo:") for item in forbidden)


# =============================================================================
# The observed cast does not close the ontology
# =============================================================================


def test_scope_narrowing_from_the_observed_cast_cannot_authorize_a_repair(
    product_client,
) -> None:
    """Case 5: the debugger's own closed-world step must not become warrant.

    This is the incident's row 1, reduced: a rule quantified over robots, a
    story showing robots of one kind, and an argument that the distinction is
    immaterial *in this story*.
    """
    client, adapter = product_client
    project = adapter._project_record(None).id
    adapter._get_canon_review_store(project)
    client.post(
        "/governor/fiction/world-rules",
        json={"project_id": project, "rule": "Robots cannot perceive ghosts."},
    )
    client.post(
        "/governor/fiction/world-rules",
        json={
            "project_id": project,
            "rule": "These are the only robots in Doverton.",
        },
    )

    candidate = adapter._get_canon_review_store(project).add(
        kind="world_rule",
        subject="robots",
        statement="only the robots currently in the story are covered by this rule",
        warrant="established_closure",
        target_anchor_id="world-1",
        category="robots",
    )
    response = client.post(
        f"/governor/fiction/capture/{candidate.id}/accept", json={"project_id": project}
    )

    assert response.status_code == 422
    assert "not state that category is complete" in response.json()["detail"]
    rules = client.get("/governor/fiction/world-rules").json()["rules"]
    assert rules[0]["rule"] == "Robots cannot perceive ghosts."


def test_authoritative_enumeration_permits_closed_world_reasoning(product_client) -> None:
    """Case 4: when the author closes the category, narrowing is licensed."""
    client, adapter = product_client
    project = adapter._project_record(None).id
    adapter._get_canon_review_store(project)
    client.post(
        "/governor/fiction/world-rules",
        json={"project_id": project, "rule": "Robots cannot perceive ghosts."},
    )
    client.post(
        "/governor/fiction/world-rules",
        json={
            "project_id": project,
            "rule": "These are the only robots in Doverton: Jacqueline, Hope, and Misty.",
            "closes_category": "robots",
        },
    )

    candidate = adapter._get_canon_review_store(project).add(
        kind="world_rule",
        subject="robots",
        statement="the rule covers Jacqueline, Hope, and Misty",
        warrant="established_closure",
        target_anchor_id="world-1",
        category="robots",
    )
    response = client.post(
        f"/governor/fiction/capture/{candidate.id}/accept", json={"project_id": project}
    )

    assert response.status_code == 200


def test_a_later_subtype_does_not_make_the_earlier_rule_repairable(product_client) -> None:
    """Case 3: refinement is not contradiction, and does not close anything."""
    client, adapter = product_client
    project = adapter._project_record(None).id
    adapter._get_canon_review_store(project)
    for rule in (
        "Robots cannot perceive ghosts.",
        "Synthetic beings include robots, uploaded humans, and constructs.",
    ):
        client.post("/governor/fiction/world-rules", json={"project_id": project, "rule": rule})

    candidate = adapter._get_canon_review_store(project).add(
        kind="world_rule",
        subject="robots",
        statement="the new subtype makes the earlier rule inconsistent",
        warrant="contradictory_state",
        target_anchor_id="world-1",
        category="synthetic beings",
    )
    response = client.post(
        f"/governor/fiction/capture/{candidate.id}/accept", json={"project_id": project}
    )

    assert response.status_code == 422
    assert len(client.get("/governor/fiction/world-rules").json()["rules"]) == 2


def test_a_candidate_making_no_extent_claim_is_unaffected(product_client) -> None:
    """Case 6: the gate only engages when a candidate reasons about extent."""
    client, adapter = product_client
    project = adapter._project_record(None).id
    adapter._get_canon_review_store(project)

    candidate = adapter._get_canon_review_store(project).add(
        kind="constraint",
        subject="Halo",
        statement="serve again, because Army policy bars former specialists",
        warrant="dropped_subject",
    )
    response = client.post(
        f"/governor/fiction/capture/{candidate.id}/accept", json={"project_id": project}
    )

    assert response.status_code == 200
    forbidden = client.get("/governor/fiction/forbidden").json()["forbidden"]
    assert any(item["description"].startswith("Halo:") for item in forbidden)


def test_favicon_is_served_instead_of_404(product_client) -> None:
    """Browsers request /favicon.ico unconditionally; production logged a 404 for
    every page load until this route existed."""
    client, _ = product_client

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert response.content.startswith(b"<svg")


def test_the_page_declares_its_icon(product_client) -> None:
    client, _ = product_client
    assert 'rel="icon"' in client.get("/").text


def test_startup_resumes_maintenance_a_process_replacement_stranded(
    product_client, monkeypatch
) -> None:
    """Durable checkpoints used to sit idle until the writer tried again.

    Progress is process-local, so a container replacement mid-run left finished
    chunks on disk with nothing carrying them forward, and the writer paid the
    latency a second time for work already done.
    """
    from gov_webui.context_summary import SummaryWork, source_for, utc_now

    client, adapter = product_client
    session = _seed_long_product_session(client, adapter)
    context_store = _enable_test_budget(adapter, monkeypatch)
    context_store.save_work(
        SummaryWork(
            source=source_for(session, session.messages[:4]),
            generator_model="claude-context-summary",
            chunks=[],
            updated_at=utc_now(),
        )
    )
    scheduled: list[dict] = []
    monkeypatch.setattr(
        adapter, "_schedule_context_maintenance", lambda **kwargs: scheduled.append(kwargs)
    )

    resumed = adapter._reconcile_interrupted_maintenance()

    assert resumed == [session.id]
    assert [item["session_id"] for item in scheduled] == [session.id]


def test_reconciliation_is_bounded(product_client, monkeypatch) -> None:
    """A cold start serves the writer first; catching up is not urgent."""
    client, adapter = product_client
    _seed_long_product_session(client, adapter)
    context_store = _enable_test_budget(adapter, monkeypatch)
    monkeypatch.setattr(
        context_store.__class__,
        "interrupted_sessions",
        lambda self: [f"session-{index}" for index in range(50)],
    )
    scheduled: list[dict] = []
    monkeypatch.setattr(
        adapter, "_schedule_context_maintenance", lambda **kwargs: scheduled.append(kwargs)
    )

    resumed = adapter._reconcile_interrupted_maintenance()

    assert len(resumed) == adapter.MAINTENANCE_RECONCILE_LIMIT
    assert len(scheduled) == adapter.MAINTENANCE_RECONCILE_LIMIT


def test_reconciliation_never_stops_the_application_from_serving(
    product_client, monkeypatch
) -> None:
    """Every failure here is swallowed; startup must not depend on it."""
    client, adapter = product_client
    monkeypatch.setattr(
        adapter, "_get_library_store", lambda: (_ for _ in ()).throw(RuntimeError("no library"))
    )

    assert adapter._reconcile_interrupted_maintenance() == []
