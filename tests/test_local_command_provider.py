# SPDX-License-Identifier: Apache-2.0
"""Deterministic qualification for the typed local-command provider."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest

from gov_webui import provider_cli
from gov_webui.model_providers import (
    AnthropicMessagesTransport,
    LocalCommandTransport,
    ProviderConfigurationError,
    ProviderError,
    ProviderChunk,
    ProviderResponse,
    load_provider_catalog,
)


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!/usr/bin/env python3\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


def _catalog(
    tmp_path: Path,
    executable: Path,
    *,
    timeout_seconds: float = 2,
):
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    config = tmp_path / "providers.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "command-writing",
                "providers": [
                    {
                        "id": "command-provider",
                        "protocol": "local-command",
                        "command": {
                            "adapter": "kimi-code",
                            "executable_env": "TEST_COMMAND_PATH",
                            "working_directory_env": "TEST_COMMAND_WORKDIR",
                        },
                        "timeout_seconds": timeout_seconds,
                        "models": [
                            {
                                "id": "command-writing",
                                "model": "provider/model-alias",
                                "label": "Command writing model",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    environ = {
        "TEST_COMMAND_PATH": str(executable),
        "TEST_COMMAND_WORKDIR": str(workdir),
    }
    return load_provider_catalog(config), environ


def _claude_catalog(tmp_path: Path, executable: Path):
    workdir = tmp_path / "claude-work"
    workdir.mkdir(exist_ok=True)
    config = tmp_path / "claude-providers.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "claude-writing",
                "providers": [
                    {
                        "id": "claude-local",
                        "protocol": "local-command",
                        "command": {
                            "adapter": "claude-code",
                            "executable_env": "TEST_CLAUDE_PATH",
                            "working_directory_env": "TEST_CLAUDE_WORKDIR",
                        },
                        "models": [
                            {
                                "id": "claude-writing",
                                "model": "sonnet",
                                "label": "Claude Sonnet",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    environ = {
        "TEST_CLAUDE_PATH": str(executable),
        "TEST_CLAUDE_WORKDIR": str(workdir),
    }
    return load_provider_catalog(config), environ


def test_local_command_configuration_is_typed_and_explicit(tmp_path: Path) -> None:
    executable = _executable(tmp_path / "command", "print()\n")
    catalog, environ = _catalog(tmp_path, executable)
    model = catalog.require_available("command-writing", environ=environ)

    assert model.protocol == "local-command"
    assert model.provider_id == "command-provider"
    assert model.model_id == "provider/model-alias"
    assert model.command is not None
    assert model.command.adapter == "kimi-code"
    assert model.command.executable_env == "TEST_COMMAND_PATH"
    assert model.command.working_directory_env == "TEST_COMMAND_WORKDIR"


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda p: p["command"].update({"adapter": "unknown"}), "unsupported"),
        (lambda p: p["command"].update({"executable_env": "not an env"}), "environment"),
        (lambda p: p.update({"base_url": "https://provider.example"}), "cannot define"),
        (lambda p: p["models"][0].pop("model"), "non-empty string"),
        (lambda p: p["command"].update({"arbitrary": True}), "unsupported fields"),
    ],
)
def test_invalid_local_command_configuration_fails_clearly(
    tmp_path: Path, mutation, expected: str
) -> None:
    executable = _executable(tmp_path / "command", "print()\n")
    catalog, _ = _catalog(tmp_path, executable)
    config_path = tmp_path / "providers.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    mutation(config["providers"][0])
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ProviderConfigurationError, match=expected):
        load_provider_catalog(config_path)

    assert catalog.default_model == "command-writing"


def test_missing_or_unusable_command_fails_without_fallback(tmp_path: Path) -> None:
    executable = _executable(tmp_path / "command", "print()\n")
    catalog, environ = _catalog(tmp_path, executable)

    with pytest.raises(ProviderError) as missing:
        catalog.require_available("command-writing", environ={})
    assert missing.value.code == "unavailable_command"

    environ["TEST_COMMAND_PATH"] = str(tmp_path / "absent")
    with pytest.raises(ProviderError) as absent:
        catalog.require_available("command-writing", environ=environ)
    assert absent.value.code == "unavailable_command"
    assert absent.value.provider_id == "command-provider"


@pytest.mark.asyncio
async def test_kimi_adapter_constructs_command_and_parses_final_assistant(
    tmp_path: Path,
) -> None:
    executable = _executable(
        tmp_path / "command",
        """import json
import sys
print(json.dumps({"role": "meta", "type": "system.version", "version": "fixture"}))
print(json.dumps({"role": "assistant", "content": "intermediate"}))
print(json.dumps({"role": "tool", "content": "ignored"}))
print(json.dumps({"role": "assistant", "content": [{"type": "text", "text": json.dumps(sys.argv[1:])}]}))
""",
    )
    catalog, environ = _catalog(tmp_path, executable)
    model = catalog.resolve("command-writing")

    response = await LocalCommandTransport(model, environ=environ).complete("Write briefly.")

    assert json.loads(response.content) == [
        "--model",
        "provider/model-alias",
        "--prompt",
        "Write briefly.",
        "--output-format",
        "stream-json",
    ]
    assert response.model_id == "provider/model-alias"
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_claude_adapter_uses_stdin_and_parses_json_result(tmp_path: Path) -> None:
    executable = _executable(
        tmp_path / "claude",
        """import json
import sys
prompt = sys.stdin.read()
print(json.dumps({
    "result": json.dumps({"arguments": sys.argv[1:], "prompt": prompt}),
    "usage": {"input_tokens": 11, "output_tokens": 7},
}))
""",
    )
    catalog, environ = _claude_catalog(tmp_path, executable)

    response = await LocalCommandTransport(
        catalog.resolve("claude-writing"), environ=environ
    ).complete("Keep the voice spare.")

    result = json.loads(response.content)
    assert result["arguments"] == [
        "--print",
        "--output-format",
        "json",
        "--verbose",
        "--model",
        "sonnet",
    ]
    assert result["prompt"] == "Keep the voice spare."
    assert response.model_id == "sonnet"
    assert response.usage == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }


@pytest.mark.asyncio
async def test_claude_auth_refusal_is_normalized_without_stderr_leak(
    tmp_path: Path,
) -> None:
    executable = _executable(
        tmp_path / "claude",
        """import sys
print("not logged in; private authentication detail", file=sys.stderr)
raise SystemExit(1)
""",
    )
    catalog, environ = _claude_catalog(tmp_path, executable)

    with pytest.raises(ProviderError) as caught:
        await LocalCommandTransport(catalog.resolve("claude-writing"), environ=environ).complete(
            "Hello"
        )

    assert caught.value.code == "command_refused"
    assert caught.value.status_code == 403
    assert str(caught.value) == ("Claude Code authentication or usage authorization was refused")
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
async def test_claude_structured_auth_error_fails_closed(tmp_path: Path) -> None:
    executable = _executable(
        tmp_path / "claude",
        """import json
print(json.dumps([{
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "result": "API Error: 401 OAuth access token has expired",
}]))
""",
    )
    catalog, environ = _claude_catalog(tmp_path, executable)

    with pytest.raises(ProviderError) as caught:
        await LocalCommandTransport(catalog.resolve("claude-writing"), environ=environ).complete(
            "Hello"
        )

    assert caught.value.code == "command_refused"
    assert caught.value.status_code == 403
    assert str(caught.value) == ("Claude Code authentication or usage authorization was refused")


@pytest.mark.asyncio
async def test_usage_window_refusal_is_normalized_without_stderr_leak(
    tmp_path: Path,
) -> None:
    executable = _executable(
        tmp_path / "command",
        """import sys
print("private path and credential-shaped diagnostic: quota usage limit", file=sys.stderr)
raise SystemExit(1)
""",
    )
    catalog, environ = _catalog(tmp_path, executable)

    with pytest.raises(ProviderError) as caught:
        await LocalCommandTransport(catalog.resolve("command-writing"), environ=environ).complete(
            "Hello"
        )

    assert caught.value.code == "usage_limit"
    assert caught.value.status_code == 403
    assert str(caught.value) == "Kimi Code usage window is exhausted"
    assert "private path" not in str(caught.value)


@pytest.mark.asyncio
async def test_malformed_command_response_is_normalized(tmp_path: Path) -> None:
    executable = _executable(tmp_path / "command", 'print("not-json")\n')
    catalog, environ = _catalog(tmp_path, executable)

    with pytest.raises(ProviderError) as caught:
        await LocalCommandTransport(catalog.resolve("command-writing"), environ=environ).complete(
            "Hello"
        )

    assert caught.value.code == "malformed_response"


@pytest.mark.asyncio
async def test_command_timeout_stops_process(tmp_path: Path) -> None:
    executable = _executable(
        tmp_path / "command",
        """import time
time.sleep(60)
""",
    )
    catalog, environ = _catalog(tmp_path, executable, timeout_seconds=0.1)

    with pytest.raises(ProviderError) as caught:
        await LocalCommandTransport(catalog.resolve("command-writing"), environ=environ).complete(
            "Hello"
        )

    assert caught.value.code == "timeout"


@pytest.mark.asyncio
async def test_command_cancellation_stops_process_and_propagates(tmp_path: Path) -> None:
    executable = _executable(
        tmp_path / "command",
        """import time
time.sleep(60)
""",
    )
    catalog, environ = _catalog(tmp_path, executable)
    task = asyncio.create_task(
        LocalCommandTransport(catalog.resolve("command-writing"), environ=environ).complete("Hello")
    )
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_dispatcher_translates_command_response_to_codex_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    executable = _executable(tmp_path / "command", "print()\n")
    _, environ = _catalog(tmp_path, executable)
    monkeypatch.setenv("MARGINALIA_MODEL_CONFIG", str(tmp_path / "providers.json"))
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(provider_cli.sys, "stdin", io.StringIO("governed prompt"))

    async def fake_complete(self, prompt: str) -> ProviderResponse:
        assert self.model.provider_id == "command-provider"
        assert prompt == "governed prompt"
        return ProviderResponse(
            content="command result",
            model_id="provider/model-alias",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            finish_reason="stop",
        )

    monkeypatch.setattr(LocalCommandTransport, "complete", fake_complete)

    result = provider_cli.main(
        ["exec", "--json", "--skip-git-repo-check", "-m", "command-writing", "-"]
    )

    assert result == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events == [
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "command result"},
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    ]


def test_dispatcher_reports_normalized_command_failure_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    executable = _executable(tmp_path / "command", "print()\n")
    _, environ = _catalog(tmp_path, executable)
    monkeypatch.setenv("MARGINALIA_MODEL_CONFIG", str(tmp_path / "providers.json"))
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(provider_cli.sys, "stdin", io.StringIO("governed prompt"))

    async def fake_complete(self, prompt: str) -> ProviderResponse:
        raise ProviderError(
            "usage_limit",
            "Kimi Code usage window is exhausted",
            provider_id=self.model.provider_id,
            model_id=self.model.model_id,
            status_code=403,
        )

    monkeypatch.setattr(LocalCommandTransport, "complete", fake_complete)

    result = provider_cli.main(["exec", "--json", "-m", "command-writing", "-"])
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert captured.err == "Kimi Code usage window is exhausted\n"


def test_dispatcher_routes_anthropic_to_native_messages_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "providers.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "anthropic-writing",
                "providers": [
                    {
                        "id": "anthropic-api",
                        "protocol": "anthropic-messages",
                        "base_url": "https://api.anthropic.test/v1",
                        "api_key_env": "ANTHROPIC_TEST_KEY",
                        "models": [
                            {
                                "id": "anthropic-writing",
                                "model": "claude-test-model",
                                "label": "Anthropic model",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MARGINALIA_MODEL_CONFIG", str(config))
    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "fixture-secret")
    monkeypatch.setattr(provider_cli.sys, "stdin", io.StringIO("governed prompt"))

    async def fake_stream(self, messages, **kwargs):
        assert self.model.provider_id == "anthropic-api"
        assert messages == [{"role": "user", "content": "governed prompt"}]
        yield ProviderChunk(content="native reply")
        yield ProviderChunk(
            usage={"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8},
            finish_reason="end_turn",
        )

    monkeypatch.setattr(AnthropicMessagesTransport, "stream", fake_stream)

    result = provider_cli.main(["exec", "--json", "-m", "anthropic-writing", "-"])

    assert result == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events == [
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "native reply"},
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 6, "output_tokens": 2},
        },
    ]
