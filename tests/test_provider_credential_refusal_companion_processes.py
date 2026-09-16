# SPDX-License-Identifier: Apache-2.0
"""Exact-binary witness: missing credential is a definitive pre-dispatch refusal.

A catalog model whose providerd credential was never provisioned must be
refused with the definitive ``unavailable`` wire code — no reservation is
written, nothing is sent, and endpoint readiness reports the endpoint
``credential_unavailable``. This is the regression witness for the
2026-09-15 incident where the same defect wedged custody as ``unknown``.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from gov_webui.ag_provider_config import render_provider_configs
from gov_webui.ag_provider_gateway import AgProviderGateway
from gov_webui.generation_executor import ProviderOutcomeUnknown, ProviderRefusedBeforeSend
from gov_webui.generation_secrets import create_provider_rpc_identities


PROVIDERD = os.environ.get("MARGINALIA_TEST_AG_PROVIDERD")
PROVIDERCTL = os.environ.get("MARGINALIA_TEST_AG_PROVIDERCTL")
pytestmark = pytest.mark.skipif(
    not PROVIDERD or not PROVIDERCTL or os.geteuid() != 0,
    reason="exact provider binaries and an isolated root-owned filesystem were not supplied",
)


def _relocate(config: str, root: Path, secrets: Path) -> str:
    replacements = {
        "/var/lib/agent-governor/providerd/providerd.db": str(root / "providerd.db"),
        "/var/lib/agent-governor/providerd/objects": str(root / "objects"),
        "/run/marginalia/providerd/provider.sock": str(root / "socket" / "provider.sock"),
        "/run/credentials/marginalia-providerd/rpc.pk8": str(secrets / "providerd" / "rpc.pk8"),
        "/run/credentials/marginalia-generation-worker/rpc.pk8": str(
            secrets / "providerctl" / "rpc.pk8"
        ),
        "/etc/marginalia/providerd.toml": str(root / "providerd.toml"),
    }
    for source, target in replacements.items():
        config = config.replace(source, target)
    return config


def test_missing_credential_is_definitive_refusal_without_reserved_residue(
    tmp_path: Path,
) -> None:
    assert PROVIDERD is not None and PROVIDERCTL is not None
    os.chmod(tmp_path, 0o700)
    secrets = tmp_path / "secrets"
    _, _, metadata = create_provider_rpc_identities(secrets)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    socket_parent = tmp_path / "socket"
    socket_parent.mkdir(mode=0o2750)
    socket_parent.chmod(0o2750)
    (tmp_path / "objects").mkdir(mode=0o700)
    models = tmp_path / "providers.json"
    models.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "openai-writer",
                "providers": [
                    {
                        "id": "openai-api",
                        "protocol": "openai-compatible",
                        "base_url": "https://api.fixture.invalid/v1",
                        "api_key_env": "FIXTURE_API_KEY",
                        "models": [
                            {"id": "openai-writer", "model": "openai-physical", "label": "API"}
                        ],
                    },
                    {
                        "id": "ollama-local",
                        "protocol": "openai-compatible",
                        "inference": "local",
                        "base_url": "http://127.0.0.1:9/v1",
                        "models": [
                            {"id": "orion-writer", "model": "orion-physical", "label": "Orion"}
                        ],
                    },
                    {
                        "id": "claude-command",
                        "protocol": "local-command",
                        "command": {
                            "adapter": "claude-code",
                            "executable_env": "CLAUDE_COMMAND_PATH",
                            "working_directory_env": "CLAUDE_COMMAND_WORKDIR",
                        },
                        "models": [{"id": "claude-writer", "model": "sonnet", "label": "Claude"}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    os.chmod(models, 0o600)
    daemon, client = render_provider_configs(
        models,
        metadata,
        environment={
            # Deliberately not installed: readiness must report this endpoint
            # command_unavailable without the daemon executing anything.
            "CLAUDE_COMMAND_PATH": str(tmp_path / "not-installed" / "claude"),
            "CLAUDE_COMMAND_WORKDIR": str(work),
        },
    )
    daemon_path = tmp_path / "providerd.toml"
    client_path = tmp_path / "providerctl.toml"
    daemon_path.write_text(_relocate(daemon, tmp_path, secrets), encoding="utf-8")
    client_path.write_text(_relocate(client, tmp_path, secrets), encoding="utf-8")
    daemon_path.chmod(0o600)
    client_path.chmod(0o600)

    subprocess.run(
        [PROVIDERD, "--check-config", "--config", str(daemon_path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [PROVIDERCTL, "--check-config", "--config", str(client_path)],
        check=True,
        capture_output=True,
    )

    # The credential directory exists but holds no openai-api-key: the exact
    # production defect from the incident handoff.
    credentials = tmp_path / "credentials"
    credentials.mkdir(mode=0o700)
    daemon_process = subprocess.Popen(
        [PROVIDERD, "--config", str(daemon_path)],
        env={"CREDENTIALS_DIRECTORY": str(credentials)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        socket = socket_parent / "provider.sock"
        deadline = time.monotonic() + 5
        while not socket.exists() and daemon_process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not socket.exists() or daemon_process.poll() is not None:
            if daemon_process.poll() is None:
                daemon_process.terminate()
                daemon_process.wait(timeout=5)
            error = daemon_process.stderr.read() if daemon_process.stderr else ""
            pytest.fail(f"isolated provider daemon did not become ready: {error[-2000:]}")

        gateway = AgProviderGateway(Path(PROVIDERCTL), client_path, models)
        assert gateway.endpoint_readiness() == {
            "openai-api": "credential_unavailable",
            "ollama-local": "ready",
            "claude-command": "command_unavailable",
        }

        transaction = gateway.prepare(
            {
                "context_id": "synthetic-context",
                "messages": [{"role": "user", "content": "Must not leave the host."}],
                "model": "openai-writer",
            },
            project_id="synthetic-project",
            session_id="synthetic-session",
            docket_attempt="sha256:" + "1" * 64,
            docket_marker="sha256:" + "2" * 64,
            actual_route="openai-api",
        )
        assert transaction["dispatch"].startswith("sha256:")

        with pytest.raises(ProviderRefusedBeforeSend):
            gateway.execute(transaction, selected_model="openai-writer")

        # No reservation residue: providerd never committed the dispatch, so
        # the deterministic dispatch id is simply unknown to it.
        with pytest.raises(ProviderOutcomeUnknown, match="not_found"):
            gateway.fetch(transaction["dispatch"], selected_model="openai-writer")
    finally:
        if daemon_process.poll() is None:
            daemon_process.terminate()
            daemon_process.wait(timeout=5)
