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
    monkeypatch.setattr(adapter, "MARGINALIA_AG_NG_ONLY", False)
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
    adapter._generation_stores.clear()
    adapter._manuscript_stores.clear()
    adapter._snapshot_stores.clear()
    adapter._context_summary_stores.clear()
    adapter._context_maintenance_adapters.clear()
    adapter._context_maintenance_tasks.clear()
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
    adapter._generation_stores.clear()
    adapter._manuscript_stores.clear()
    adapter._snapshot_stores.clear()
    adapter._context_summary_stores.clear()
    adapter._context_maintenance_adapters.clear()
    adapter._context_maintenance_tasks.clear()
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


def test_backend_discovery_is_owned_by_ag_ng_providerd(provider_client) -> None:
    client, adapter = provider_client
    adapter._governed_chat_adapter.reset_mock()

    response = client.get("/v1/backends")

    assert response.status_code == 200
    assert response.json()["authoritative"] == "ag-ng"
    assert {item["type"] for item in response.json()["backends"]} == {
        "provider-a",
        "provider-b",
    }
    assert all(item["configured_by"] == "ag-providerd" for item in response.json()["backends"])
    adapter._governed_chat_adapter.provider.assert_not_awaited()
    adapter._governed_chat_adapter.models.assert_not_awaited()


def test_api_info_names_ag_ng_without_classic_probe(provider_client) -> None:
    client, adapter = provider_client
    adapter._governed_chat_adapter.reset_mock()

    response = client.get("/api/info")

    assert response.status_code == 200
    assert response.json()["backend"] == "ag-ng-providerd"
    assert response.json()["provider_owner"] == "ag-ng-providerd"
    adapter._governed_chat_adapter.provider.assert_not_awaited()


def test_conflict_resolution_proposes_canon_without_accepting_response(provider_client) -> None:
    client, adapter = provider_client
    project = adapter._project_record(None)
    session = adapter._get_session_store(project.id).create(
        project.context_id, model="fiction-model"
    )
    store = adapter._get_generation_store(project.id)
    created = store.create_request(
        client_request_id="conflict-client",
        project_id=project.id,
        session_id=session.id,
        expected_revision=session.revision,
        canon_fingerprint="sha256:canon",
        guidance_fingerprint="sha256:guidance",
        original_model="fiction-model",
        original_route="provider-a",
        request={
            "context_id": project.context_id,
            "messages": [{"role": "user", "content": "Continue."}],
            "model": "fiction-model",
        },
    ).request
    store.set_dispatch_enabled(project.id, True)
    dispatch = store.reserve_dispatch(created.id)
    store.mark_executing(dispatch.id, "provider-attempt")
    candidate = store.record_candidate(
        dispatch.id,
        response_digest="sha256:response",
        evidence_ref="evidence://candidate",
    )
    store.block_candidate(
        candidate.id,
        "response conflicts with accepted story constraints",
        detail={
            "conflict": {
                "violations": [
                    {
                        "anchor_id": "forbid-1",
                        "anchor_type": "prohibition",
                        "severity": "reject",
                        "description": "Forbidden event",
                        "evidence": [],
                    }
                ],
                "recommended_action": "fail_closed",
                "checked_anchors": 1,
            }
        },
    )

    pending = client.get(f"/v1/governed-chat/pending?project_id={project.id}")
    assert pending.status_code == 200
    assert pending.json()["pending"]["candidate_id"] == candidate.id

    resolved = client.post(
        "/v1/governed-chat/resolve",
        json={
            "action": "revise",
            "candidate_id": candidate.id,
            "project_id": project.id,
            "new_anchor_text": "The sigil may appear only in dreams.",
        },
    )
    assert resolved.status_code == 200
    proposal = adapter._get_canon_review_store(project.id).get(resolved.json()["proposal_id"])
    assert proposal.warrant == "author_revision"
    assert proposal.target_anchor_id == "forbid-1"
    assert store.get_candidate(candidate.id).accepted_message_id is None
    assert (
        client.get(f"/v1/governed-chat/pending?project_id={project.id}").json()["pending"] is None
    )


def test_context_maintenance_model_is_not_writer_selectable(provider_client) -> None:
    client, adapter = provider_client
    config_path = Path(adapter.MARGINALIA_MODEL_CONFIG)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["providers"][0]["models"].append(
        {
            "id": "internal-context-summary",
            "model": "upstream-summary",
            "label": "Internal context summary",
            "purpose": "context-maintenance",
        }
    )
    config_path.write_text(json.dumps(config), encoding="utf-8")

    listed = client.get("/v1/models")
    assert listed.status_code == 200
    assert "internal-context-summary" not in {item["id"] for item in listed.json()["data"]}
    assert client.get("/v1/models/internal-context-summary").status_code == 404

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "internal-context-summary",
            "messages": [{"role": "user", "content": "Do not run."}],
        },
    )
    assert response.status_code == 422
    assert adapter._governed_chat_adapter.chat_send.await_count == 0


def test_model_api_and_new_session_use_an_available_default(provider_client) -> None:
    client, adapter = provider_client
    config_path = Path(adapter.MARGINALIA_MODEL_CONFIG)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["providers"][0]["api_key_env"] = "UNSET_PROVIDER_TEST_KEY"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json()["default_model"] == "fiction-model"
    assert response.json()["data"][0]["available"] is True
    assert response.json()["data"][1]["available"] is True

    created = client.post("/sessions/", json={"title": "Available default"})
    assert created.status_code == 200
    # Provider availability is owned by credential-isolated providerd. The web
    # process retains the configured selection instead of probing secrets.
    assert created.json()["model"] == "fiction-model"

    refused = client.post(
        "/sessions/",
        json={"title": "Explicit unavailable", "model": "fiction-model"},
    )
    assert refused.status_code == 200


def test_operator_unavailable_model_is_visible_but_disabled(provider_client) -> None:
    client, adapter = provider_client
    config_path = Path(adapter.MARGINALIA_MODEL_CONFIG)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    model = config["providers"][1]["models"][0]
    model["availability"] = "unavailable"
    model["unavailable_reason"] = "Configured model is unavailable for this account."
    config_path.write_text(json.dumps(config), encoding="utf-8")

    listed = client.get("/v1/models")
    assert listed.status_code == 200
    unavailable = next(item for item in listed.json()["data"] if item["id"] == "fiction-model-b")
    assert unavailable["available"] is False
    assert unavailable["unavailable_reason"] == model["unavailable_reason"]
    detail = client.get("/v1/models/fiction-model-b")
    assert detail.status_code == 200
    assert detail.json()["available"] is False

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "fiction-model-b",
            "messages": [{"role": "user", "content": "Do not dispatch."}],
        },
    )
    assert response.status_code == 503
    adapter._governed_chat_adapter.chat_send.assert_not_awaited()


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
    assert adapter._governed_chat_adapter.chat_send.await_args.kwargs["model"] == "fiction-model"


def test_accounting_refuses_provider_identity_that_differs_from_frozen_selection(
    provider_client,
) -> None:
    _, adapter = provider_client

    with pytest.raises(ValueError, match="frozen dispatch"):
        adapter._message_accounting(
            "fiction-model",
            "provider-a",
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            execution_identity={
                "configured_provider_id": "substituted-provider",
                "configured_model_id": "upstream-a",
                "observed_provider_id": None,
                "observed_model_id": "upstream-a",
                "observed_status": "attested",
            },
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
    assert response.json()["outcome"] == "failure"
    assert response.json()["failure_type"] == "invalid_result"
    assert "different model" in response.json()["message"]


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
            "outcome": "authored",
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
            "outcome": "authored",
        },
    )
    assert second.status_code == 200

    messages = client.get(f"/sessions/{session_id}").json()["messages"]
    assert [(message["provider_id"], message["model_id"]) for message in messages] == [
        ("provider-a", "upstream-a"),
        ("provider-b", "upstream-b"),
    ]


def test_openrouter_model_is_writer_selectable_through_the_model_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway participates through the normal catalog surface.

    A writer-facing model is listed, reports availability from its credential
    variable, and carries the exact provider and upstream identity Marginalia
    records on authored messages.
    """
    import gov_webui.adapter as adapter

    config_path = tmp_path / "providers.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "openrouter-glm-5.3-flash",
                "providers": [
                    {
                        "id": "openrouter",
                        "protocol": "openai-compatible",
                        "base_url": "https://openrouter.ai/api/v1",
                        "api_key_env": "OPENROUTER_API_KEY",
                        "timeout_seconds": 180,
                        "models": [
                            {
                                "id": "openrouter-glm-5.3-flash",
                                "model": "z-ai/glm-5.3-flash",
                                "label": "GLM 5.3 Flash (OpenRouter)",
                            },
                            {
                                "id": "openrouter-glm-5.3-summary",
                                "model": "z-ai/glm-5.3-flash",
                                "label": "GLM 5.3 Flash context maintenance",
                                "purpose": "context-maintenance",
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(adapter, "MARGINALIA_MODEL_CONFIG", str(config_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    catalog = adapter._configured_provider_catalog()
    assert catalog is not None

    writer_models = [model for model in catalog.models if model.purpose == "writing"]
    assert [model.id for model in writer_models] == ["openrouter-glm-5.3-flash"]

    # Without the credential the model is visible but disabled, never substituted.
    listed = writer_models[0]
    assert listed.availability_error() == (
        "required credential environment variable OPENROUTER_API_KEY is not set"
    )

    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder-not-a-real-key")
    resolved = adapter._configured_provider_catalog().require_available("openrouter-glm-5.3-flash")
    assert resolved.provider_id == "openrouter"
    assert resolved.model_id == "z-ai/glm-5.3-flash"

    # The internal maintenance purpose remains hidden from writer selection.
    maintenance = adapter._configured_provider_catalog().resolve("openrouter-glm-5.3-summary")
    assert maintenance.purpose == "context-maintenance"


@pytest.fixture()
def taxonomy_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """One catalog covering every writer-visible category at once."""
    import gov_webui.adapter as adapter

    config_path = tmp_path / "providers.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "hosted-writing",
                "providers": [
                    {
                        "id": "hosted",
                        "protocol": "openai-compatible",
                        "base_url": "https://hosted.test/v1",
                        "api_key_env": "TAXONOMY_TEST_KEY",
                        "models": [
                            {
                                "id": "hosted-writing",
                                "model": "upstream-hosted",
                                "label": "Hosted writing model",
                            }
                        ],
                    },
                    {
                        "id": "ollama",
                        "protocol": "openai-compatible",
                        "inference": "local",
                        "base_url": "http://ollama.test/v1",
                        "models": [
                            {
                                "id": "orion-test",
                                "model": "orion-test",
                                "label": "Orion 26B",
                            }
                        ],
                    },
                    {
                        "id": "agent",
                        "protocol": "existing-command",
                        "models": [{"id": "agent-writing", "label": "Codex"}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("TAXONOMY_TEST_KEY", "present")
    monkeypatch.setattr(adapter, "MARGINALIA_ENABLE_DONOR_ROUTES", False)
    monkeypatch.setattr(adapter, "GOVERNOR_CONTEXTS_DIR", str(tmp_path / "contexts"))
    monkeypatch.setattr(adapter, "GOVERNOR_CONTEXT_ID", "taxonomy-test")
    monkeypatch.setattr(adapter, "GOVERNOR_MODE", "fiction")
    monkeypatch.setattr(adapter, "GOVERNOR_AUTH_TOKEN", "")
    monkeypatch.setattr(adapter, "MARGINALIA_MODEL_CONFIG", str(config_path))
    yield TestClient(adapter.app)


def test_model_menu_reports_locality_and_kind_for_every_entry(taxonomy_client) -> None:
    """The writer's menu must say where inference runs, from declared state."""
    payload = taxonomy_client.get("/v1/models").json()

    assert [
        (item["id"], item["kind"], item["inference"], item["access"], item["category_label"])
        for item in payload["data"]
    ] == [
        ("hosted-writing", "model", "hosted", "api", "Hosted models"),
        ("orion-test", "model", "local", "open", "Local models"),
        # An agent binary runs here; its inference does not. Naming this group
        # "local" would spend the word the writer needs for actual privacy.
        ("agent-writing", "agent", "hosted", "subscription", "Subscription agents"),
    ]


def test_single_model_endpoint_reports_the_same_taxonomy(taxonomy_client) -> None:
    """One authority: the list and the detail view cannot classify differently."""
    listed = {item["id"]: item for item in taxonomy_client.get("/v1/models").json()["data"]}

    for model_id, expected in listed.items():
        detail = taxonomy_client.get(f"/v1/models/{model_id}").json()
        assert [detail[key] for key in ("kind", "inference", "access", "category")] == [
            expected[key] for key in ("kind", "inference", "access", "category")
        ]


def test_generation_records_estimated_against_observed_usage(provider_client, caplog) -> None:
    """The estimate and the provider's own count are logged side by side.

    `provider_overhead_tokens` has never been checked against what a provider
    actually counts, so it is assumed rather than known. Emitting both numbers
    per turn is what makes it characterisable. This is economics: it is recorded
    after the fact and never feeds an admission decision.
    """
    import json as _json
    import logging

    client, adapter = provider_client
    created = client.post(
        "/sessions/",
        json={"title": "Accounting", "model": "fiction-model", "project_id": "default"},
    ).json()

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fiction-model",
                "project_id": "default",
                "session_id": created["id"],
                "messages": [{"role": "user", "content": "Begin the story."}],
            },
        )
    assert response.status_code == 200

    lines = [r.getMessage() for r in caplog.records if "request_accounting" in r.getMessage()]
    assert lines, "a configured-model generation must record accounting"
    payload = _json.loads(lines[-1].split("request_accounting ", 1)[1])

    assert payload["provider_id"] == "provider-a"
    assert payload["model_id"] == "upstream-a"
    # This fixture has no bounded budget, so there is no estimate — but what the
    # call cost is still recorded, and the delta is honestly absent.
    assert payload["estimated_prompt_tokens"] is None
    assert payload["prompt_delta"] is None
    # The two sources stay separate keys; neither defaults into the other.
    assert "observed_prompt_tokens" in payload
    assert "prompt_delta" in payload
    assert payload["latency_ms"] >= 0
