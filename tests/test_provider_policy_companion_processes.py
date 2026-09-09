# SPDX-License-Identifier: Apache-2.0
"""Exact-binary production-shaped provider policy composition witness."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gov_webui.ag_provider_config import render_provider_configs
from gov_webui.ag_provider_gateway import AgProviderGateway
from gov_webui.generation_executor import ExecutorError
from gov_webui.generation_secrets import create_provider_rpc_identities


PROVIDERD = os.environ.get("MARGINALIA_TEST_AG_PROVIDERD")
PROVIDERCTL = os.environ.get("MARGINALIA_TEST_AG_PROVIDERCTL")
pytestmark = pytest.mark.skipif(
    not PROVIDERD or not PROVIDERCTL or os.geteuid() != 0,
    reason="exact provider binaries and an isolated root-owned filesystem were not supplied",
)


def _catalog(path: Path, local_port: int) -> None:
    path.write_text(
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
                        "id": "anthropic-api",
                        "protocol": "anthropic-messages",
                        "base_url": "https://anthropic.fixture.invalid/v1",
                        "api_key_env": "FIXTURE_ANTHROPIC_KEY",
                        "models": [
                            {
                                "id": "anthropic-writer",
                                "model": "anthropic-physical",
                                "label": "Anthropic",
                            }
                        ],
                    },
                    {
                        "id": "ollama-local",
                        "protocol": "openai-compatible",
                        "inference": "local",
                        "base_url": f"http://127.0.0.1:{local_port}/v1",
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
                    {
                        "id": "kimi-command",
                        "protocol": "local-command",
                        "command": {
                            "adapter": "kimi-code",
                            "executable_env": "KIMI_COMMAND_PATH",
                            "working_directory_env": "KIMI_COMMAND_WORKDIR",
                        },
                        "models": [{"id": "kimi-writer", "model": "kimi-k3", "label": "Kimi"}],
                    },
                    {
                        "id": "codex-command",
                        "protocol": "existing-command",
                        "models": [{"id": "codex-writer", "label": "Codex"}],
                    },
                    {
                        "id": "retained-unavailable",
                        "protocol": "openai-compatible",
                        "base_url": "https://unavailable.fixture.invalid/v1",
                        "api_key_env": "UNAVAILABLE_KEY",
                        "models": [
                            {
                                "id": "future-writer",
                                "model": "future-physical",
                                "label": "Future",
                                "availability": "unavailable",
                                "unavailable_reason": "Not enrolled in this deployment.",
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


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


def test_exact_daemon_loads_and_providerctl_routes_production_shaped_catalog(
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
    fake_command = tmp_path / "fake-provider"
    fake_command.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        '  *\' --print \'*) printf \'%s\\n\' \'{"type":"result","result":"claude fixture","usage":{}}\' ;;\n'
        '  *\' --prompt \'*) printf \'%s\\n\' \'{"role":"assistant","content":"kimi fixture"}\' ;;\n'
        '  *) printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"agent_message","text":"codex fixture"}}\' \'{"type":"turn.completed","usage":{}}\' ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    fake_command.chmod(0o700)
    received_local: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            request = json.loads(body)
            received_local.append(request)
            response = json.dumps(
                {
                    "model": request["model"],
                    "choices": [{"message": {"content": "orion fixture"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    models = tmp_path / "providers.json"
    _catalog(models, server.server_port)
    daemon, client = render_provider_configs(
        models,
        metadata,
        environment={
            "CLAUDE_COMMAND_PATH": str(fake_command),
            "CLAUDE_COMMAND_WORKDIR": str(work),
            "KIMI_COMMAND_PATH": str(fake_command),
            "KIMI_COMMAND_WORKDIR": str(work),
            "CODEX_NATIVE_PATH": str(fake_command),
            "CODEX_COMMAND_WORKDIR": str(work),
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

    gateway = AgProviderGateway(Path(PROVIDERCTL), client_path, models)
    expected = {
        "openai-writer": "openai-api",
        "anthropic-writer": "anthropic-api",
        "orion-writer": "ollama-local",
        "claude-writer": "claude-command",
        "kimi-writer": "kimi-command",
        "codex-writer": "codex-command",
    }
    transactions: dict[str, dict[str, object]] = {}
    for sequence, (selection, route) in enumerate(expected.items(), 1):
        transaction = gateway.prepare(
            {
                "context_id": "synthetic-context",
                "messages": [{"role": "user", "content": "Synthetic parity prompt."}],
                "model": selection,
            },
            project_id="synthetic-project",
            session_id=f"synthetic-session-{sequence}",
            docket_attempt="sha256:" + f"{sequence:064x}",
            docket_marker="sha256:" + f"{sequence + 100:064x}",
            actual_route=route,
        )
        assert transaction["dispatch"].startswith("sha256:")
        transactions[selection] = transaction

    with pytest.raises(ExecutorError, match="unavailable"):
        gateway.prepare(
            {
                "context_id": "synthetic-context",
                "messages": [{"role": "user", "content": "Must not route."}],
                "model": "future-writer",
            },
            project_id="synthetic-project",
            session_id="synthetic-unavailable",
            docket_attempt="sha256:" + "f" * 64,
            docket_marker="sha256:" + "e" * 64,
            actual_route="retained-unavailable",
        )

    credentials = tmp_path / "credentials"
    credentials.mkdir(mode=0o700)
    for name in ("fixture-api-key", "fixture-anthropic-key"):
        credential = credentials / name
        credential.write_text("synthetic-credential", encoding="utf-8")
        credential.chmod(0o600)
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
        for selection in (
            "orion-writer",
            "claude-writer",
            "kimi-writer",
            "codex-writer",
        ):
            try:
                response = gateway.execute(transactions[selection], selected_model=selection)
            except Exception as exc:
                daemon_process.terminate()
                daemon_process.wait(timeout=5)
                error = daemon_process.stderr.read() if daemon_process.stderr else ""
                pytest.fail(
                    f"{selection} did not settle through the isolated daemon: {exc}; "
                    f"daemon={error[-2000:]}"
                )
            assert response["outcome"] == "authored"
        assert received_local[0]["model"] == "orion-physical"
        assert received_local[0]["max_tokens"] == 4096
    finally:
        if daemon_process.poll() is None:
            daemon_process.terminate()
            daemon_process.wait(timeout=5)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
