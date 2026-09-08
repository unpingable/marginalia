# SPDX-License-Identifier: Apache-2.0
"""ag-ng provider deployment configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gov_webui.ag_provider_config import render_provider_configs
from gov_webui.generation_secrets import create_provider_rpc_identities


def _write_catalog(path: Path, providers: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": providers[0]["models"][0]["id"],  # type: ignore[index]
                "providers": providers,
            }
        ),
        encoding="utf-8",
    )


def test_configs_bind_separate_identities_and_never_embed_api_secret(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    _, _, metadata = create_provider_rpc_identities(secrets)
    models = tmp_path / "providers.json"
    _write_catalog(
        models,
        [
            {
                "id": "remote",
                "protocol": "openai-compatible",
                "base_url": "https://example.invalid/v1",
                "api_key_env": "REMOTE_SECRET",
                "models": [{"id": "writer", "model": "model-x", "label": "Writer"}],
            }
        ],
    )

    daemon, client = render_provider_configs(
        models, metadata, environment={"REMOTE_SECRET": "must-not-appear"}
    )

    identity = json.loads(metadata.read_text(encoding="utf-8"))
    assert identity["providerd"]["principal"] in daemon
    assert identity["providerctl"]["principal"] in daemon
    assert identity["providerctl"]["principal"] in client
    assert identity["providerd"]["principal"] in client
    assert "must-not-appear" not in daemon + client
    assert 'credential_name = "remote-secret"' in daemon
    assert 'url = "https://example.invalid/v1/chat/completions"' in daemon


def test_local_plaintext_requires_declared_local_inference(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    _, _, metadata = create_provider_rpc_identities(secrets)
    models = tmp_path / "providers.json"
    _write_catalog(
        models,
        [
            {
                "id": "unsafe",
                "protocol": "openai-compatible",
                "base_url": "http://example.invalid/v1",
                "models": [{"id": "writer", "model": "model-x", "label": "Writer"}],
            }
        ],
    )

    with pytest.raises(ValueError, match="declared local"):
        render_provider_configs(models, metadata)


def test_command_environment_is_a_closed_nonsecret_allowlist(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    _, _, metadata = create_provider_rpc_identities(secrets)
    models = tmp_path / "providers.json"
    _write_catalog(
        models,
        [
            {
                "id": "claude",
                "protocol": "local-command",
                "command": {
                    "adapter": "claude-code",
                    "executable_env": "CLAUDE_COMMAND_PATH",
                    "working_directory_env": "CLAUDE_COMMAND_WORKDIR",
                },
                "models": [{"id": "writer", "model": "sonnet", "label": "Claude writer"}],
            }
        ],
    )

    daemon, _ = render_provider_configs(
        models,
        metadata,
        environment={
            "CLAUDE_COMMAND_PATH": "/opt/claude/claude",
            "CLAUDE_COMMAND_WORKDIR": "/work",
            "HOME": "/run/claude-home",
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "must-not-appear",
        },
    )

    assert 'adapter = "claude-code"' in daemon
    assert 'executable = "/opt/claude/claude"' in daemon
    assert 'working_directory = "/work"' in daemon
    assert 'HOME = "/var/lib/marginalia/provider-home"' in daemon
    assert "/run/claude-home" not in daemon
    assert "ANTHROPIC_API_KEY" not in daemon
    assert "must-not-appear" not in daemon
