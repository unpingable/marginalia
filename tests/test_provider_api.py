# SPDX-License-Identifier: Apache-2.0
"""Application-boundary qualification for explicit model selection and provenance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from support import fake_governed_chat


@pytest.fixture()
def provider_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import gov_webui.adapter as adapter

    config_path = tmp_path / "providers.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "fiction-model",
                "providers": [
                    {
                        "id": "provider-a",
                        "protocol": "openai-compatible",
                        "base_url": "http://provider-a.test/v1",
                        "models": [
                            {
                                "id": "fiction-model",
                                "model": "upstream-a",
                                "label": "Fiction A",
                            }
                        ],
                    },
                    {
                        "id": "provider-b",
                        "protocol": "openai-compatible",
                        "base_url": "http://provider-b.test/v1",
                        "models": [
                            {
                                "id": "fiction-model-b",
                                "model": "upstream-b",
                                "label": "Fiction B",
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(adapter, "MARGINALIA_ENABLE_DONOR_ROUTES", False)
    monkeypatch.setattr(adapter, "GOVERNOR_CONTEXTS_DIR", str(tmp_path / "contexts"))
    monkeypatch.setattr(adapter, "GOVERNOR_CONTEXT_ID", "provider-test")
    monkeypatch.setattr(adapter, "GOVERNOR_MODE", "fiction")
    monkeypatch.setattr(adapter, "GOVERNOR_AUTH_TOKEN", "")
    monkeypatch.setattr(adapter, "MARGINALIA_MODEL_CONFIG", str(config_path))
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
    adapter._governed_chat_adapter = fake_governed_chat(
        content="Configured response",
        model="fiction-model",
    )

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
    adapter._governed_chat_adapter = None


def test_configured_model_list_is_explicit(provider_client) -> None:
    client, _ = provider_client

    response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json()["default_model"] == "fiction-model"
    assert [
        (item["id"], item["provider_id"], item["model_id"], item["label"])
        for item in response.json()["data"]
    ] == [
        ("fiction-model", "provider-a", "upstream-a", "Fiction A"),
        ("fiction-model-b", "provider-b", "upstream-b", "Fiction B"),
    ]


def test_chat_response_records_exact_configured_identity(provider_client) -> None:
    client, adapter = provider_client

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "fiction-model",
            "messages": [{"role": "user", "content": "Continue."}],
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == "fiction-model"
    assert response.json()["provider_id"] == "provider-a"
    assert response.json()["model_id"] == "upstream-a"
    assert (
        adapter._governed_chat_adapter.chat_send.await_args.kwargs["model"]
        == "fiction-model"
    )


def test_unknown_configured_model_refuses_before_generation(provider_client) -> None:
    client, adapter = provider_client

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "not-enabled",
            "messages": [{"role": "user", "content": "Continue."}],
        },
    )

    assert response.status_code == 422
    adapter._governed_chat_adapter.chat_send.assert_not_awaited()


def test_daemon_model_substitution_is_not_accepted(provider_client) -> None:
    client, adapter = provider_client
    adapter._governed_chat_adapter = fake_governed_chat(
        content="Wrong backend result",
        model="fiction-model-b",
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "fiction-model",
            "messages": [{"role": "user", "content": "Continue."}],
        },
    )

    assert response.status_code == 502
    assert "different model" in response.json()["detail"]


def test_conversation_switch_preserves_historical_response_identity(
    provider_client,
) -> None:
    client, _ = provider_client

    created = client.post(
        "/sessions/",
        json={"title": "Model history", "model": "fiction-model"},
    )
    assert created.status_code == 200
    session_id = created.json()["id"]

    first = client.post(
        f"/sessions/{session_id}/messages",
        json={
            "role": "assistant",
            "content": "Before switch",
            "model": "fiction-model",
            "provider_id": "provider-a",
            "model_id": "upstream-a",
        },
    )
    assert first.status_code == 200

    switched = client.patch(
        f"/sessions/{session_id}",
        json={"model": "fiction-model-b"},
    )
    assert switched.status_code == 200
    assert switched.json()["model"] == "fiction-model-b"

    second = client.post(
        f"/sessions/{session_id}/messages",
        json={
            "role": "assistant",
            "content": "After switch",
            "model": "fiction-model-b",
            "provider_id": "provider-b",
            "model_id": "upstream-b",
        },
    )
    assert second.status_code == 200

    messages = client.get(f"/sessions/{session_id}").json()["messages"]
    assert [
        (message["provider_id"], message["model_id"])
        for message in messages
    ] == [
        ("provider-a", "upstream-a"),
        ("provider-b", "upstream-b"),
    ]
