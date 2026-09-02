# SPDX-License-Identifier: Apache-2.0
"""Failure-containment qualification across governor/provider boundaries."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from governor.chat_bridge import ChatBridge, CodexBackend
from governor.context_manager import GovernorContextManager
from governor.daemon import DaemonState, serve_unix

from gov_webui.daemon_client import (
    DaemonChatClient,
    DaemonTimeoutError,
    _read_message,
    _write_message,
)
from gov_webui.model_providers import (
    OpenAICompatibleTransport,
    ProviderError,
    load_provider_catalog,
)
from gov_webui.reliability import GovernorProgress
from gov_webui.synthetic_worker import _append_record, probe_once


class SequencedDaemon:
    """First connection stalls until cancellation; later connections reply."""

    def __init__(self) -> None:
        self.connections = 0
        self.active = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        connection = self.connections
        self.active += 1
        try:
            request = await _read_message(reader)
            assert request is not None
            if connection % 2:
                await reader.read()
                return
            await _write_message(
                writer,
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {"content": "recovered", "model": "fixture"},
                },
            )
        finally:
            self.active -= 1
            writer.close()
            await writer.wait_closed()


@pytest.mark.asyncio
async def test_wedged_rpc_releases_client_serialization_and_next_request_succeeds(
    tmp_path: Path,
) -> None:
    fixture = SequencedDaemon()
    socket_path = tmp_path / "governor.sock"
    server = await asyncio.start_unix_server(fixture.handle, path=socket_path)
    client = DaemonChatClient(socket_path, rpc_timeout_seconds=0.2, chat_timeout_seconds=0.2)
    try:
        first = asyncio.create_task(client.chat_send([{"role": "user", "content": "A"}]))
        await asyncio.sleep(0.02)
        second = asyncio.create_task(client.chat_send([{"role": "user", "content": "B"}]))

        with pytest.raises(DaemonTimeoutError, match="deadline"):
            await first
        assert (await second)["content"] == "recovered"
        await asyncio.sleep(0.02)
        assert fixture.active == 0
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_rpc_cancellation_resets_connection_and_next_request_succeeds(
    tmp_path: Path,
) -> None:
    fixture = SequencedDaemon()
    socket_path = tmp_path / "governor.sock"
    server = await asyncio.start_unix_server(fixture.handle, path=socket_path)
    client = DaemonChatClient(socket_path, rpc_timeout_seconds=1, chat_timeout_seconds=1)
    try:
        first = asyncio.create_task(client.chat_send([{"role": "user", "content": "A"}]))
        await asyncio.sleep(0.02)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await client.chat_send([{"role": "user", "content": "B"}]))[
            "content"
        ] == "recovered"
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_repeated_rpc_failures_do_not_accumulate_connections(tmp_path: Path) -> None:
    fixture = SequencedDaemon()
    socket_path = tmp_path / "governor.sock"
    server = await asyncio.start_unix_server(fixture.handle, path=socket_path)
    client = DaemonChatClient(socket_path, rpc_timeout_seconds=0.05, chat_timeout_seconds=0.05)
    try:
        for _ in range(4):
            with pytest.raises(DaemonTimeoutError):
                await client.chat_send([{"role": "user", "content": "stall"}])
            assert (await client.chat_send([{"role": "user", "content": "next"}]))[
                "content"
            ] == "recovered"
            # This local fixture deliberately closes after one response; clear
            # the corresponding client framing state before the next cycle.
            await client.close()
        await asyncio.sleep(0.02)
        assert fixture.active == 0
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


def _http_model(tmp_path: Path, *, timeout: float = 0.1):
    config = tmp_path / "providers.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "fixture",
                "providers": [
                    {
                        "id": "fixture-http",
                        "protocol": "openai-compatible",
                        "base_url": "http://provider.test/v1",
                        "timeout_seconds": timeout,
                        "connect_timeout_seconds": timeout,
                        "read_timeout_seconds": timeout,
                        "models": [{"id": "fixture", "model": "upstream", "label": "Fixture"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_provider_catalog(config).resolve("fixture")


@pytest.mark.asyncio
async def test_provider_connects_but_never_completes_hits_total_deadline(
    tmp_path: Path,
) -> None:
    model = _http_model(tmp_path)
    never = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        await never.wait()
        raise AssertionError("unreachable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as caught:
            await OpenAICompatibleTransport(model, client=client).complete(
                [{"role": "user", "content": "A"}]
            )
    assert caught.value.code == "deadline_exceeded"


class ContinuousStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):
        while True:
            await asyncio.sleep(0.02)
            yield (
                b'data: {"model":"upstream","choices":[{"delta":{"content":"x"},'
                b'"finish_reason":null}]}\n\n'
            )

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_continuously_active_network_stream_still_hits_total_deadline(
    tmp_path: Path,
) -> None:
    model = _http_model(tmp_path)
    body = ContinuousStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body)

    chunks = 0
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as caught:
            async for _chunk in OpenAICompatibleTransport(model, client=client).stream(
                [{"role": "user", "content": "A"}]
            ):
                chunks += 1
    assert caught.value.code == "deadline_exceeded"
    assert chunks >= 2
    assert body.closed is True


def _pid_gone(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1]
    except FileNotFoundError:
        return True
    return state.split()[0] == "Z"


def test_outer_supervisor_reaps_hanging_provider_descendants_and_recovers(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "provider.pid"
    child_file = tmp_path / "descendant.pid"
    hanging = tmp_path / "hanging-provider"
    hanging.write_text(
        """#!/usr/bin/env python3
import os, subprocess, sys, time
from pathlib import Path
Path(os.environ['FIXTURE_PROVIDER_PID']).write_text(str(os.getpid()))
child = subprocess.Popen(
    [sys.executable, '-c', 'import time; time.sleep(60)'],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    start_new_session=True,
)
Path(os.environ['FIXTURE_DESCENDANT_PID']).write_text(str(child.pid))
time.sleep(60)
""",
        encoding="utf-8",
    )
    hanging.chmod(0o755)
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(Path.cwd() / "src"),
            "CODEX_NATIVE_PATH": str(hanging),
            "FIXTURE_PROVIDER_PID": str(pid_file),
            "FIXTURE_DESCENDANT_PID": str(child_file),
            "MARGINALIA_GOVERNOR_INVOCATION_TIMEOUT_SECONDS": "0.2",
            "MARGINALIA_PROVIDER_CLEANUP_GRACE_SECONDS": "0.2",
        }
    )
    started = time.monotonic()
    failed = subprocess.run(
        [sys.executable, "-m", "gov_webui.provider_supervisor", "exec", "-"],
        input=b"A",
        capture_output=True,
        env=env,
        check=False,
    )
    assert failed.returncode == 1
    assert time.monotonic() - started < 2
    assert b"timed out after 0.2 seconds" in failed.stderr

    pids = [int(pid_file.read_text()), int(child_file.read_text())]
    for _ in range(100):
        if all(_pid_gone(pid) for pid in pids):
            break
        time.sleep(0.01)
    assert all(_pid_gone(pid) for pid in pids)

    healthy = tmp_path / "healthy-provider"
    healthy.write_text(
        """#!/usr/bin/env python3
import json
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'recovered'}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
""",
        encoding="utf-8",
    )
    healthy.chmod(0o755)
    env["CODEX_NATIVE_PATH"] = str(healthy)
    recovered = subprocess.run(
        [sys.executable, "-m", "gov_webui.provider_supervisor", "exec", "-"],
        input=b"B",
        capture_output=True,
        env=env,
        check=False,
    )
    assert recovered.returncode == 0
    assert b"recovered" in recovered.stdout


def test_outer_supervisor_cleans_descendant_left_by_successful_provider(
    tmp_path: Path,
) -> None:
    child_file = tmp_path / "leaked-descendant.pid"
    provider = tmp_path / "returning-provider"
    provider.write_text(
        """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
child = subprocess.Popen(
    [sys.executable, '-c', 'import time; time.sleep(60)'],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    start_new_session=True,
)
Path(os.environ['FIXTURE_DESCENDANT_PID']).write_text(str(child.pid))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'done'}}))
print(json.dumps({'type':'turn.completed','usage':{}}))
""",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(Path.cwd() / "src"),
            "CODEX_NATIVE_PATH": str(provider),
            "FIXTURE_DESCENDANT_PID": str(child_file),
            "MARGINALIA_GOVERNOR_INVOCATION_TIMEOUT_SECONDS": "2",
            "MARGINALIA_PROVIDER_CLEANUP_GRACE_SECONDS": "0.2",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "gov_webui.provider_supervisor", "exec", "-"],
        input=b"A",
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    child_pid = int(child_file.read_text())
    assert _pid_gone(child_pid)


def test_governor_progress_distinguishes_liveness_from_capacity_readiness() -> None:
    progress = GovernorProgress(wedge_seconds=0.01)
    token = progress.begin("fixture")
    time.sleep(0.02)
    assert progress.snapshot()["wedged"] is True
    progress.failed(token, "governor_timeout", capacity_uncertain=True)
    assert progress.snapshot()["ready"] is False
    recovery = progress.begin("fixture")
    progress.succeeded(recovery)
    assert progress.snapshot()["ready"] is True


@pytest.mark.asyncio
async def test_real_governor_serialization_recovers_after_provider_tree_hang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider_pid = tmp_path / "provider.pid"
    descendant_pid = tmp_path / "descendant.pid"
    provider = tmp_path / "sequenced-provider"
    provider.write_text(
        """#!/usr/bin/env python3
import json, os, subprocess, sys, time
from pathlib import Path
prompt = sys.stdin.read()
if 'WEDGE' in prompt:
    Path(os.environ['FIXTURE_PROVIDER_PID']).write_text(str(os.getpid()))
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    Path(os.environ['FIXTURE_DESCENDANT_PID']).write_text(str(child.pid))
    time.sleep(60)
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'B_RECOVERED'}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
""",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    monkeypatch.setenv("CODEX_NATIVE_PATH", str(provider))
    monkeypatch.setenv("FIXTURE_PROVIDER_PID", str(provider_pid))
    monkeypatch.setenv("FIXTURE_DESCENDANT_PID", str(descendant_pid))
    monkeypatch.setenv("MARGINALIA_GOVERNOR_INVOCATION_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("MARGINALIA_PROVIDER_CLEANUP_GRACE_SECONDS", "0.2")
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("PYTHONPATH", str(Path.cwd() / "src"))

    daemon_dir = tmp_path / ".governor"
    daemon_dir.mkdir()
    state = DaemonState(daemon_dir, mode="fiction")
    contexts = GovernorContextManager(daemon_dir)
    state._context_manager = contexts
    state._chat_bridge = ChatBridge(
        backend=CodexBackend(codex_path=str(Path.cwd() / "codex-provider.sh")),
        context_manager=contexts,
        show_ok_footer=False,
    )
    state._backend_type = "codex"
    state._backend_kwargs = {"default_model": "codex-default"}
    socket_path = tmp_path / "real-governor.sock"
    server_task = asyncio.create_task(serve_unix(socket_path, state))
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    client = DaemonChatClient(socket_path, rpc_timeout_seconds=1, chat_timeout_seconds=2)
    try:
        with pytest.raises(RuntimeError, match="timed out after 0.2 seconds"):
            await client.chat_send(
                [{"role": "user", "content": "WEDGE"}],
                model="codex-default",
                context_id="synthetic",
            )
        result = await client.chat_send(
            [{"role": "user", "content": "B"}],
            model="codex-default",
            context_id="synthetic",
        )
        assert result["content"] == "B_RECOVERED"
        assert result["receipt"]["receipt_id"]
        pids = [int(provider_pid.read_text()), int(descendant_pid.read_text())]
        for _ in range(100):
            if all(_pid_gone(pid) for pid in pids):
                break
            await asyncio.sleep(0.01)
        assert all(_pid_gone(pid) for pid in pids)
    finally:
        await client.close()
        server_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await server_task


async def _read_http_request(
    reader: asyncio.StreamReader,
) -> None:
    headers = await reader.readuntil(b"\r\n\r\n")
    content_length = 0
    for line in headers.decode("ascii").splitlines():
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1])
    if content_length:
        await reader.readexactly(content_length)


@pytest.mark.asyncio
async def test_synthetic_probe_emits_bounded_machine_readable_pass_and_fail(
    tmp_path: Path,
) -> None:
    async def passing(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _read_http_request(reader)
        body = json.dumps(
            {
                "status": "PASS",
                "backend": "fixture",
                "receipt_id": "r_synthetic",
                "context_id": "fixture-synthetic",
            }
        ).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    pass_server = await asyncio.start_server(passing, "127.0.0.1", 0)
    pass_port = pass_server.sockets[0].getsockname()[1]
    passed = await probe_once(
        base_url=f"http://127.0.0.1:{pass_port}",
        model="fixture-model",
        timeout_seconds=1,
        marker="qualification",
    )
    pass_server.close()
    await pass_server.wait_closed()
    assert passed["result"] == "PASS"
    assert passed["backend"] == "fixture"
    assert passed["latency_ms"] > 0

    async def hanging(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _read_http_request(reader)
        await reader.read()
        writer.close()
        await writer.wait_closed()

    fail_server = await asyncio.start_server(hanging, "127.0.0.1", 0)
    fail_port = fail_server.sockets[0].getsockname()[1]
    failed = await probe_once(
        base_url=f"http://127.0.0.1:{fail_port}",
        model="fixture-model",
        timeout_seconds=0.1,
        marker="qualification",
    )
    fail_server.close()
    await fail_server.wait_closed()
    assert failed["result"] == "FAIL"
    assert failed["failure_class"] == "deadline_exceeded"
    assert failed["latency_ms"] < 1000

    record_path = tmp_path / "synthetics.jsonl"
    _append_record(record_path, passed)
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    assert stored["receipt_id"] == "r_synthetic"
    assert stored["result"] == "PASS"
