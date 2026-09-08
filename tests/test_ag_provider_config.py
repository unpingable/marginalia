# SPDX-License-Identifier: Apache-2.0
"""ag-ng provider deployment configuration."""

from __future__ import annotations

import json
import tomllib
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
    assert 'kind = "credentialed_https_api"' in daemon
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


def test_remote_https_cannot_become_credentialless(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    _, _, metadata = create_provider_rpc_identities(secrets)
    models = tmp_path / "providers.json"
    _write_catalog(
        models,
        [
            {
                "id": "unsafe-remote",
                "protocol": "openai-compatible",
                "base_url": "https://example.invalid/v1",
                "models": [{"id": "writer", "model": "model-x", "label": "Writer"}],
            }
        ],
    )

    with pytest.raises(ValueError, match="unsafe-remote.*requires an enrolled credential"):
        render_provider_configs(models, metadata)


def test_local_plaintext_has_exact_origin_allowlist_and_denied_redirects(
    tmp_path: Path,
) -> None:
    secrets = tmp_path / "secrets"
    _, _, metadata = create_provider_rpc_identities(secrets)
    models = tmp_path / "providers.json"
    _write_catalog(
        models,
        [
            {
                "id": "orion",
                "protocol": "openai-compatible",
                "base_url": "http://host.docker.internal:11434/v1",
                "inference": "local",
                "models": [{"id": "writer", "model": "orion", "label": "Orion"}],
            }
        ],
    )

    daemon, _ = render_provider_configs(models, metadata)

    assert 'kind = "local_http"' in daemon
    assert 'allowed_origins = ["http://host.docker.internal:11434"]' in daemon
    assert 'redirect_policy = "deny"' in daemon
    assert "credential_name" not in daemon


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
    assert 'kind = "command"' in daemon
    assert 'executable = "/opt/claude/claude"' in daemon
    assert 'working_directory = "/work"' in daemon
    assert 'HOME = "/var/lib/marginalia/provider-home"' in daemon
    assert "/run/claude-home" not in daemon
    assert "ANTHROPIC_API_KEY" not in daemon
    assert "must-not-appear" not in daemon


def test_command_model_selection_distinguishes_explicit_and_provider_default(
    tmp_path: Path,
) -> None:
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
                "models": [{"id": "claude-writer", "model": "sonnet", "label": "Claude"}],
            },
            {
                "id": "codex",
                "protocol": "existing-command",
                "models": [{"id": "codex-default", "label": "Codex"}],
            },
        ],
    )

    daemon, _ = render_provider_configs(
        models,
        metadata,
        environment={
            "CLAUDE_COMMAND_PATH": "/opt/claude/claude",
            "CLAUDE_COMMAND_WORKDIR": "/work",
        },
    )

    assert daemon.count('model_argument = "required"') == 1
    assert daemon.count('model_argument = "omit"') == 1


def test_duplicate_erinfacing_selections_share_one_physical_model_policy(
    tmp_path: Path,
) -> None:
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
                "models": [
                    {"id": "writer", "model": "sonnet", "label": "Writer"},
                    {"id": "writer-fast", "model": "sonnet", "label": "Writer fast"},
                ],
            }
        ],
    )

    daemon, _ = render_provider_configs(
        models,
        metadata,
        environment={
            "CLAUDE_COMMAND_PATH": "/opt/claude/claude",
            "CLAUDE_COMMAND_WORKDIR": "/work",
        },
    )

    assert daemon.count('id = "sonnet"') == 1


def test_production_shaped_catalog_preserves_all_transport_routes(tmp_path: Path) -> None:
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
                "api_key_env": "REMOTE_KEY",
                "models": [{"id": "remote-writer", "model": "remote-model", "label": "Remote"}],
            },
            {
                "id": "orion",
                "protocol": "openai-compatible",
                "base_url": "http://host.docker.internal:11434/v1",
                "inference": "local",
                "models": [{"id": "orion-writer", "model": "orion", "label": "Orion"}],
            },
            {
                "id": "claude",
                "protocol": "local-command",
                "command": {
                    "adapter": "claude-code",
                    "executable_env": "CLAUDE_COMMAND_PATH",
                    "working_directory_env": "CLAUDE_COMMAND_WORKDIR",
                },
                "models": [{"id": "claude-writer", "model": "sonnet", "label": "Claude"}],
            },
        ],
    )

    daemon, _ = render_provider_configs(
        models,
        metadata,
        environment={
            "CLAUDE_COMMAND_PATH": "/opt/claude/claude",
            "CLAUDE_COMMAND_WORKDIR": "/work",
        },
    )
    policy = tomllib.loads(daemon)
    endpoints = {endpoint["id"]: endpoint for endpoint in policy["endpoints"]}

    assert policy["schema"] == "ag.config.providerd.v2"
    assert set(endpoints) == {"remote", "orion", "claude"}
    assert endpoints["remote"]["transport"]["kind"] == "credentialed_https_api"
    assert endpoints["orion"]["transport"] == {
        "kind": "local_http",
        "url": "http://host.docker.internal:11434/v1/chat/completions",
        "allowed_origins": ["http://host.docker.internal:11434"],
        "redirect_policy": "deny",
    }
    assert endpoints["claude"]["transport"]["kind"] == "command"
    assert endpoints["claude"]["transport"]["command"]["adapter"] == "claude-code"
