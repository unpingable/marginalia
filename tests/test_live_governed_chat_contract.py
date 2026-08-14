"""Live Marginalia↔AG contract regression over a real Unix socket."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from governor.continuity import Anchor, AnchorType, Severity, create_registry
from governor.context_manager import GovernorContextManager
from gov_webui.daemon_client import DaemonChatClient
from gov_webui.governed_chat_adapter import (
    GovernedChatAdapter,
    GovernedChatContractError,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DAEMON_HELPER = REPO_ROOT / "tests" / "helpers" / "fake_governor_daemon.py"


async def _start_daemon(data_root: Path, socket_path: Path):
    env = os.environ.copy()
    ag_src = Path(__import__("governor").__file__).resolve().parents[1]
    marginalia_src = REPO_ROOT / "src"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(marginalia_src), str(ag_src), env.get("PYTHONPATH", "")]
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(DAEMON_HELPER),
        "--data-root",
        str(data_root),
        "--socket",
        str(socket_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    for _ in range(100):
        if socket_path.is_socket():
            return process
        if process.returncode is not None:
            stdout, stderr = await process.communicate()
            raise AssertionError(
                "deterministic AG daemon exited early:\n"
                f"stdout={stdout.decode()}\nstderr={stderr.decode()}"
            )
        await asyncio.sleep(0.05)
    await _stop_daemon(process)
    raise AssertionError("deterministic AG daemon did not create its socket")


async def _stop_daemon(process) -> None:
    if process.returncode is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.communicate(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()


def _install_blocking_anchor(daemon_dir: Path, context_id: str) -> Path:
    contexts = GovernorContextManager(daemon_dir)
    context = contexts.get_or_create(context_id, mode="fiction")
    registry = create_registry(context.governor_dir)
    registry.register(
        Anchor(
            id="marginalia-deterministic-block",
            anchor_type=AnchorType.PROHIBITION,
            description="The deterministic marker must not appear.",
            forbidden_patterns=["MARGINALIA_FORBIDDEN"],
            severity=Severity.REJECT,
        )
    )
    registry.save(context.governor_dir / "continuity" / "anchors.json")
    return context.governor_dir


@pytest.mark.asyncio
async def test_block_restart_pending_isolation_resolve_authority(tmp_path: Path):
    """Block → persisted pending → restart → resolve → authority linkage."""
    data_root = tmp_path / "marginalia-data"
    daemon_dir = data_root / ".governor"
    socket_path = tmp_path / "governor.sock"
    context_id = "erin-novel"
    context_governor_dir = _install_blocking_anchor(daemon_dir, context_id)

    process = await _start_daemon(data_root, socket_path)
    adapter = GovernedChatAdapter(
        DaemonChatClient(socket_path),
        context_id=context_id,
        expected_governor_dir=daemon_dir,
    )
    try:
        contract = await adapter.contract_info()
        assert contract["governor"]["governor_dir"] == str(daemon_dir.resolve())
        assert (await adapter.provider())["type"] == "deterministic"
        assert [model["id"] for model in await adapter.models()] == [
            "deterministic-fiction-model"
        ]

        blocked = await adapter.chat_send(
            [{"role": "user", "content": "Write the next sentence."}]
        )
        assert blocked["pending"]["context_id"] == context_id
        assert blocked["receipt"]["verdict"] == "block"
        assert blocked["receipt"]["receipt_role"] == "authority"
        block_receipt_id = blocked["receipt"]["receipt_id"]
        pending_id = blocked["pending"]["id"]

        pending_file = context_governor_dir / "pending_violations.json"
        assert pending_file.is_file()
        assert not (daemon_dir / "pending_violations.json").exists()
        assert not (data_root / context_id / ".governor").exists()

        pending = await adapter.pending()
        assert pending["id"] == pending_id
        assert pending["receipt_id"] == block_receipt_id
    finally:
        await adapter.close()
        await _stop_daemon(process)

    # A new process and a new client must recover the same durable context.
    process = await _start_daemon(data_root, socket_path)
    adapter = GovernedChatAdapter(
        DaemonChatClient(socket_path),
        context_id=context_id,
        expected_governor_dir=daemon_dir,
    )
    wrong_context = GovernedChatAdapter(
        DaemonChatClient(socket_path),
        context_id="another-novel",
        expected_governor_dir=daemon_dir,
    )
    try:
        after_restart = await adapter.pending()
        assert after_restart["id"] == pending_id

        wrong_result = await wrong_context.resolve_pending(
            "proceed", reason="must not resolve Erin's context"
        )
        assert wrong_result["success"] is False
        assert (await adapter.pending())["id"] == pending_id

        resolution = await adapter.resolve_pending(
            "proceed", reason="intentional story exception"
        )
        assert resolution["success"] is True
        assert resolution["receipt"]["receipt_role"] == "authority"
        assert resolution["receipt"]["gate"] == "violation_resolution"
        assert await adapter.pending() is None
        assert not pending_file.exists()

        detail = await adapter.receipt_detail(
            resolution["receipt"]["receipt_id"]
        )
        assert detail["evidence"]["context_id"] == context_id
        assert detail["evidence"]["pending_id"] == pending_id
        assert detail["evidence"]["original_receipt_id"] == block_receipt_id

        mismatched = GovernedChatAdapter(
            DaemonChatClient(socket_path),
            context_id=context_id,
            expected_governor_dir=tmp_path / "wrong-root" / ".governor",
        )
        with pytest.raises(GovernedChatContractError, match="state-root mismatch"):
            await mismatched.contract_info()
        await mismatched.close()
    finally:
        await wrong_context.close()
        await adapter.close()
        await _stop_daemon(process)
