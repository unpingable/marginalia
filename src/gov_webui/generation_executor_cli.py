# SPDX-License-Identifier: Apache-2.0
"""Executable implementation of Docket governed-executor transport v1."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from gov_webui.daemon_client import DaemonChatClient, default_socket_path
from gov_webui.evidence_store import EncryptedEvidenceStore
from gov_webui.generation_executor import (
    ExecutorError,
    ExecutorPlan,
    GenerationExecutor,
    parse_docket_dispatch,
)
from gov_webui.governed_chat_adapter import GovernedChatAdapter


def _read_dispatch() -> bytes:
    content = sys.stdin.buffer.read(1_048_577)
    if len(content) > 1_048_576:
        raise ExecutorError("Docket dispatch exceeds 1 MiB")
    return content


async def _provider_request(payload: dict[str, Any]) -> dict[str, Any]:
    required = {"context_id", "messages", "model"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ExecutorError("frozen provider request does not have the exact v1 shape")
    if not isinstance(payload["context_id"], str) or not payload["context_id"]:
        raise ExecutorError("frozen provider request has no context identity")
    if not isinstance(payload["model"], str) or not isinstance(payload["messages"], list):
        raise ExecutorError("frozen provider request model/messages are invalid")
    governor_dir = Path(os.environ.get("GOVERNOR_DAEMON_DIR", "/data/.governor"))
    socket = os.environ.get("GOVERNOR_SOCKET") or str(default_socket_path(governor_dir))
    adapter = GovernedChatAdapter(
        DaemonChatClient(socket),
        context_id=payload["context_id"],
        expected_governor_dir=governor_dir,
    )
    try:
        return await adapter.chat_send(messages=payload["messages"], model=payload["model"])
    finally:
        await adapter.close()


def _provider(payload: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_provider_request(payload))


def _executor(plan: ExecutorPlan) -> GenerationExecutor:
    evidence = EncryptedEvidenceStore(
        plan.evidence_root,
        plan.evidence_keyring,
        retention_days=plan.evidence_retention_days,
    )
    return GenerationExecutor(plan, provider=_provider, evidence_writer=evidence.write)


def run(arguments: list[str]) -> dict[str, str] | str:
    if len(arguments) != 2:
        raise ExecutorError("usage: marginalia-generation-executor OPERATION CONFIG")
    operation, config = arguments
    plan = ExecutorPlan.from_file(Path(config))
    if operation == "plan-id":
        return plan.identity
    dispatch = parse_docket_dispatch(_read_dispatch())
    executor = _executor(plan)
    if operation == "execute":
        return executor.execute(dispatch).to_dict()
    if operation == "reconcile":
        return executor.reconcile(dispatch).to_dict()
    raise ExecutorError(f"unsupported executor operation: {operation}")


def main() -> int:
    try:
        result = run(sys.argv[1:])
        if isinstance(result, str):
            sys.stdout.write(result + "\n")
        else:
            sys.stdout.write(
                json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            )
        return 0
    except Exception as exc:
        sys.stderr.write(f"marginalia generation executor refused: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
