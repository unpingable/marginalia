# SPDX-License-Identifier: Apache-2.0
"""Marginalia's strict ag-providerctl subprocess boundary."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from gov_webui.ag_provider_gateway import AgProviderGateway
from gov_webui.generation_executor import (
    ExecutorError,
    ProviderDefinitiveFailure,
    ProviderOutcomeUnknown,
    ProviderRefusedBeforeSend,
)


def _gateway(tmp_path: Path) -> AgProviderGateway:
    model_config = tmp_path / "providers.json"
    model_config.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "writer",
                "providers": [
                    {
                        "id": "provider-route",
                        "protocol": "openai-compatible",
                        "base_url": "https://provider.invalid/v1",
                        "models": [{"id": "writer", "model": "upstream-writer", "label": "Writer"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    providerctl = tmp_path / "ag-providerctl"
    providerctl.write_text("fixture", encoding="utf-8")
    config = tmp_path / "providerctl.toml"
    config.write_text("fixture", encoding="utf-8")
    return AgProviderGateway(providerctl, config, model_config)


def _payload() -> dict:
    return {
        "context_id": "context",
        "messages": [{"role": "user", "content": "Write this."}],
        "model": "writer",
    }


def _command_gateway(tmp_path: Path, adapter: str = "claude-code") -> AgProviderGateway:
    model_config = tmp_path / "providers.json"
    model_config.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "writer",
                "providers": [
                    {
                        "id": "command-route",
                        "protocol": "local-command",
                        "command": {
                            "adapter": adapter,
                            "executable_env": "COMMAND_PATH",
                            "working_directory_env": "COMMAND_WORKDIR",
                        },
                        "models": [{"id": "writer", "model": "requested-model", "label": "Writer"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    providerctl = tmp_path / "ag-providerctl"
    providerctl.write_text("fixture", encoding="utf-8")
    config = tmp_path / "providerctl.toml"
    config.write_text("fixture", encoding="utf-8")
    return AgProviderGateway(providerctl, config, model_config)


def test_prepare_binds_frozen_route_and_exact_upstream_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _gateway(tmp_path)
    seen = {}
    dispatch = "sha256:" + "a" * 64

    def run(command, **kwargs):
        seen["command"] = command
        seen["request"] = json.loads(kwargs["input"])
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"dispatch": dispatch}).encode(), b""
        )

    monkeypatch.setattr(subprocess, "run", run)
    transaction = gateway.prepare(
        _payload(),
        project_id="project",
        session_id="session",
        docket_attempt="sha256:" + "1" * 64,
        docket_marker="sha256:" + "2" * 64,
        actual_route="provider-route",
    )

    assert transaction["dispatch"] == dispatch
    assert seen["command"][-1] == "prepare"
    assert seen["request"]["endpoint"] == "provider-route"
    assert seen["request"]["model"] == "upstream-writer"
    body = json.loads(base64.b64decode(seen["request"]["request_bytes"]))
    assert body == {
        "messages": [{"content": "Write this.", "role": "user"}],
        "max_tokens": 4096,
        "model": "upstream-writer",
        "stream": False,
    }


def test_prepare_refuses_route_substitution_before_providerctl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _gateway(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("must not run"))

    with pytest.raises(ExecutorError, match="route"):
        gateway.prepare(
            _payload(),
            project_id="project",
            session_id="session",
            docket_attempt="sha256:" + "1" * 64,
            docket_marker="sha256:" + "2" * 64,
            actual_route="substituted-route",
        )


def test_fetch_verifies_exact_event_bytes_and_normalizes_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _gateway(tmp_path)
    dispatch = "sha256:" + "d" * 64
    body = json.dumps(
        {
            "model": "upstream-writer",
            "choices": [{"message": {"content": "Accepted candidate"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    event = json.dumps(
        {
            "event": "http_response",
            "status": 200,
            "headers": {},
            "body": base64.b64encode(body).decode(),
            "protocol_terminal": True,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    exact = "sha256:" + hashlib.sha256(event).hexdigest()

    def run(command, **_kwargs):
        result = {
            "status": "ok",
            "response": {
                "kind": "inference",
                "dispatch": dispatch,
                "event_stream": base64.b64encode(event).decode(),
                "exact_event_stream": exact,
            },
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(result).encode(), b"")

    monkeypatch.setattr(subprocess, "run", run)
    response = gateway.fetch(dispatch, selected_model="writer")

    assert response["content"] == "Accepted candidate"
    assert response["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "total_tokens": 20,
    }
    assert response["provider_evidence"]["exact_event_stream"] == exact
    assert response["execution_identity"] == {
        "configured_provider_id": "provider-route",
        "configured_model_id": "upstream-writer",
        "observed_provider_id": None,
        "observed_model_id": "upstream-writer",
        "observed_status": "attested",
    }


def test_missing_http_model_is_not_promoted_from_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _gateway(tmp_path)
    dispatch = "sha256:" + "e" * 64
    body = json.dumps(
        {
            "choices": [{"message": {"content": "No physical model attestation"}}],
            "usage": {},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    event = json.dumps(
        {
            "event": "http_response",
            "status": 200,
            "headers": {},
            "body": base64.b64encode(body).decode(),
            "protocol_terminal": True,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    exact = "sha256:" + hashlib.sha256(event).hexdigest()

    def run(command, **_kwargs):
        result = {
            "status": "ok",
            "response": {
                "kind": "inference",
                "dispatch": dispatch,
                "event_stream": base64.b64encode(event).decode(),
                "exact_event_stream": exact,
            },
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(result).encode(), b"")

    monkeypatch.setattr(subprocess, "run", run)
    identity = gateway.fetch(dispatch, selected_model="writer")["execution_identity"]

    assert identity["configured_model_id"] == "upstream-writer"
    assert identity["observed_model_id"] is None
    assert identity["observed_status"] == "unavailable"


def test_command_configured_model_is_not_physical_identity_without_attestation(
    tmp_path: Path,
) -> None:
    gateway = _command_gateway(tmp_path)

    content, usage, observed = gateway._parse_body(
        gateway.catalog.resolve("writer"),
        json.dumps({"type": "result", "result": "Command response", "usage": {}}),
    )

    assert content == "Command response"
    assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert observed == {"provider_id": None, "model_id": None, "status": "unavailable"}


def test_command_records_model_only_when_tool_attests_it(tmp_path: Path) -> None:
    gateway = _command_gateway(tmp_path)

    _, _, observed = gateway._parse_body(
        gateway.catalog.resolve("writer"),
        json.dumps(
            {
                "type": "result",
                "result": "Command response",
                "model": "physically-returned-model",
                "usage": {},
            }
        ),
    )

    assert observed == {
        "provider_id": None,
        "model_id": "physically-returned-model",
        "status": "attested",
    }


def _fetch_event_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: dict
) -> tuple[AgProviderGateway, str]:
    gateway = _gateway(tmp_path)
    dispatch = "sha256:" + "f" * 64
    encoded = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    exact = "sha256:" + hashlib.sha256(encoded).hexdigest()

    def run(command, **_kwargs):
        result = {
            "status": "ok",
            "response": {
                "kind": "inference",
                "dispatch": dispatch,
                "event_stream": base64.b64encode(encoded).decode(),
                "exact_event_stream": exact,
            },
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(result).encode(), b"")

    monkeypatch.setattr(subprocess, "run", run)
    return gateway, dispatch


def test_connect_transport_failure_is_a_definitive_predispatch_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, dispatch = _fetch_event_gateway(
        tmp_path, monkeypatch, {"event": "transport_failure", "class": "connect"}
    )

    with pytest.raises(ProviderRefusedBeforeSend):
        gateway.fetch(dispatch, selected_model="writer")


def test_body_transport_failure_remains_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, dispatch = _fetch_event_gateway(
        tmp_path, monkeypatch, {"event": "transport_failure", "class": "body"}
    )

    with pytest.raises(ProviderOutcomeUnknown):
        gateway.fetch(dispatch, selected_model="writer")


def test_wire_unavailable_is_a_definitive_predispatch_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _gateway(tmp_path)

    def run(command, **_kwargs):
        result = {"status": "error", "code": "unavailable"}
        return subprocess.CompletedProcess(command, 0, json.dumps(result).encode(), b"")

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(ProviderRefusedBeforeSend):
        gateway.fetch("sha256:" + "0" * 64, selected_model="writer")


def test_wire_indeterminate_remains_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _gateway(tmp_path)

    def run(command, **_kwargs):
        result = {"status": "error", "code": "indeterminate"}
        return subprocess.CompletedProcess(command, 0, json.dumps(result).encode(), b"")

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(ProviderOutcomeUnknown):
        gateway.fetch("sha256:" + "0" * 64, selected_model="writer")


def test_endpoint_readiness_returns_exact_content_free_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _gateway(tmp_path)
    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        seen["timeout"] = kwargs.get("timeout")
        result = {
            "endpoints": [
                {"endpoint_id": "openai-api", "status": "credential_unavailable"},
                {"endpoint_id": "ollama-local", "status": "ready"},
                {"endpoint_id": "claude-command", "status": "command_unavailable"},
            ]
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(result).encode(), b"")

    monkeypatch.setattr(subprocess, "run", run)
    readiness = gateway.endpoint_readiness()

    assert readiness == {
        "openai-api": "credential_unavailable",
        "ollama-local": "ready",
        "claude-command": "command_unavailable",
    }
    assert seen["command"][-1] == "endpoint-readiness"
    assert seen["timeout"] == 30


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"endpoints": {}},
        {"endpoints": [{"endpoint_id": "openai-api"}]},
        {"endpoints": [{"endpoint_id": "openai-api", "status": "degraded"}]},
        {"endpoints": [{"endpoint_id": "", "status": "ready"}]},
        {"endpoints": [{"endpoint_id": "a", "status": "ready", "extra": True}]},
        {
            "endpoints": [
                {"endpoint_id": "a", "status": "ready"},
                {"endpoint_id": "a", "status": "ready"},
            ]
        },
    ],
)
def test_endpoint_readiness_refuses_malformed_projections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, document: dict
) -> None:
    gateway = _gateway(tmp_path)

    def run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(document).encode(), b"")

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(ExecutorError, match="malformed"):
        gateway.endpoint_readiness()


def _anthropic_gateway(tmp_path: Path) -> AgProviderGateway:
    model_config = tmp_path / "providers.json"
    model_config.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "writer",
                "providers": [
                    {
                        "id": "anthropic-route",
                        "protocol": "anthropic-messages",
                        "base_url": "https://provider.invalid/v1",
                        "api_key_env": "PROVIDER_TEST_KEY",
                        "models": [{"id": "writer", "model": "upstream-writer", "label": "Writer"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    providerctl = tmp_path / "ag-providerctl"
    providerctl.write_text("fixture", encoding="utf-8")
    config = tmp_path / "providerctl.toml"
    config.write_text("fixture", encoding="utf-8")
    return AgProviderGateway(providerctl, config, model_config)


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_openai_terminal_empty_content_is_a_definitive_failure(
    tmp_path: Path, finish_reason: str
) -> None:
    gateway = _gateway(tmp_path)
    body = json.dumps(
        {
            "model": "upstream-writer",
            "choices": [
                {
                    "message": {"role": "assistant", "content": None},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 19777, "completion_tokens": 4096},
        }
    )

    with pytest.raises(ProviderDefinitiveFailure, match="no authored text"):
        gateway._parse_body(gateway.catalog.resolve("writer"), body)


def test_openai_absent_content_with_length_finish_is_a_definitive_failure(
    tmp_path: Path,
) -> None:
    gateway = _gateway(tmp_path)
    body = json.dumps(
        {
            "model": "upstream-writer",
            "choices": [{"message": {"role": "assistant"}, "finish_reason": "length"}],
            "usage": {},
        }
    )

    with pytest.raises(ProviderDefinitiveFailure, match="no authored text"):
        gateway._parse_body(gateway.catalog.resolve("writer"), body)


@pytest.mark.parametrize("finish_reason", ["content_filter", None])
def test_openai_empty_content_without_terminal_finish_remains_unknown(
    tmp_path: Path, finish_reason: str | None
) -> None:
    gateway = _gateway(tmp_path)
    body = json.dumps(
        {
            "model": "upstream-writer",
            "choices": [
                {"message": {"role": "assistant", "content": None}, "finish_reason": finish_reason}
            ],
            "usage": {},
        }
    )

    with pytest.raises(ProviderOutcomeUnknown, match="malformed chat completion"):
        gateway._parse_body(gateway.catalog.resolve("writer"), body)


@pytest.mark.parametrize(
    "body",
    ["not json", json.dumps({"choices": [{"message": {"content": 42}}]})],
)
def test_openai_malformed_completion_remains_unknown(tmp_path: Path, body: str) -> None:
    gateway = _gateway(tmp_path)

    with pytest.raises(ProviderOutcomeUnknown, match="malformed chat completion"):
        gateway._parse_body(gateway.catalog.resolve("writer"), body)


@pytest.mark.parametrize("stop_reason", ["end_turn", "max_tokens"])
def test_anthropic_terminal_empty_content_is_a_definitive_failure(
    tmp_path: Path, stop_reason: str
) -> None:
    gateway = _anthropic_gateway(tmp_path)
    body = json.dumps(
        {
            "model": "upstream-writer",
            "content": [],
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 10, "output_tokens": 4096},
        }
    )

    with pytest.raises(ProviderDefinitiveFailure, match="no authored text"):
        gateway._parse_body(gateway.catalog.resolve("writer"), body)


def test_anthropic_empty_content_without_terminal_stop_remains_unknown(tmp_path: Path) -> None:
    gateway = _anthropic_gateway(tmp_path)
    body = json.dumps({"model": "upstream-writer", "content": [], "usage": {}})

    with pytest.raises(ProviderOutcomeUnknown, match="no authored text"):
        gateway._parse_body(gateway.catalog.resolve("writer"), body)


def test_claude_code_empty_result_is_a_definitive_failure(tmp_path: Path) -> None:
    gateway = _command_gateway(tmp_path)

    with pytest.raises(ProviderDefinitiveFailure, match="no authored text"):
        gateway._parse_body(
            gateway.catalog.resolve("writer"),
            json.dumps({"type": "result", "result": "", "usage": {}}),
        )


def test_claude_code_malformed_output_remains_unknown(tmp_path: Path) -> None:
    gateway = _command_gateway(tmp_path)

    with pytest.raises(ProviderOutcomeUnknown, match="malformed output"):
        gateway._parse_body(gateway.catalog.resolve("writer"), json.dumps({"type": "result"}))


def test_command_jsonl_without_authored_text_is_a_definitive_failure(tmp_path: Path) -> None:
    gateway = _command_gateway(tmp_path, adapter="kimi-code")
    body = '{"role":"user","content":"hi"}\n{"role":"tool","content":"ok"}\n'

    with pytest.raises(ProviderDefinitiveFailure, match="no authored text"):
        gateway._parse_body(gateway.catalog.resolve("writer"), body)


def test_command_malformed_jsonl_remains_unknown(tmp_path: Path) -> None:
    gateway = _command_gateway(tmp_path, adapter="kimi-code")

    with pytest.raises(ProviderOutcomeUnknown, match="malformed JSONL"):
        gateway._parse_body(gateway.catalog.resolve("writer"), "not json\n")
