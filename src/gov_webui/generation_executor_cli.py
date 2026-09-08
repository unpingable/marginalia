# SPDX-License-Identifier: Apache-2.0
"""Executable implementation of Docket governed-executor transport v1."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from gov_webui.ag_provider_gateway import AgProviderGateway
from gov_webui.evidence_store import EncryptedEvidenceStore
from gov_webui.generation_executor import (
    ExecutorError,
    ExecutorPlan,
    GenerationExecutor,
    parse_docket_dispatch,
)


def _read_dispatch() -> bytes:
    content = sys.stdin.buffer.read(1_048_577)
    if len(content) > 1_048_576:
        raise ExecutorError("Docket dispatch exceeds 1 MiB")
    return content


def _executor(plan: ExecutorPlan) -> GenerationExecutor:
    if None in (plan.providerctl, plan.providerctl_config, plan.model_config):
        raise ExecutorError("classic v1 executor plans are historical and cannot dispatch")
    timeout = float(os.environ.get("MARGINALIA_PROVIDER_RPC_TIMEOUT_SECONDS", "1830"))
    provider = AgProviderGateway(
        plan.providerctl,
        plan.providerctl_config,
        plan.model_config,
        timeout_seconds=timeout,
    )
    evidence = EncryptedEvidenceStore(
        plan.evidence_root,
        plan.evidence_keyring,
        retention_days=plan.evidence_retention_days,
    )
    return GenerationExecutor(plan, provider=provider, evidence_writer=evidence.write)


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
