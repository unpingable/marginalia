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
from gov_webui.generation_executor import ExecutorError


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
