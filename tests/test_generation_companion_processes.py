# SPDX-License-Identifier: Apache-2.0
"""Opt-in composition test against the exact qualified ag-ng/Docket CLIs."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gov_webui.evidence_store import create_keyring
from gov_webui.generation_boundaries import ag_digest
from gov_webui.generation_store import GenerationStore, LogicalStatus
from gov_webui.generation_worker import GovernedGeneration, WorkerConfig


AG_LOOPCTL = os.environ.get("MARGINALIA_TEST_AG_LOOPCTL")
DOCKET = os.environ.get("MARGINALIA_TEST_DOCKET")
pytestmark = pytest.mark.skipif(
    not AG_LOOPCTL or not DOCKET,
    reason="exact companion CLI paths were not supplied",
)


def _executable(path: Path, body: str) -> Path:
    source = Path(__file__).resolve().parents[1] / "src"
    path.write_text(
        "#!/usr/bin/env python3\n"
        f"import sys\nsys.path.insert(0, {str(source)!r})\n"
        + body,
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def test_exact_companions_dispatch_once_and_reopen_settled_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert AG_LOOPCTL is not None and DOCKET is not None
    contexts = tmp_path / "contexts"
    monkeypatch.setenv("GOVERNOR_CONTEXTS_DIR", str(contexts))
    context = contexts / "ctx"
    store = GenerationStore(context / "marginalia" / "generation.sqlite")
    request = store.create_request(
        client_request_id="companion-process-witness",
        project_id="project",
        session_id="session",
        expected_revision=0,
        canon_fingerprint=ag_digest("fixture", "canon"),
        guidance_fingerprint=ag_digest("fixture", "guidance"),
        original_model="fixture-model",
        original_route="fixture-route",
        request={"context_id": "ctx", "messages": [], "model": "fixture-model"},
    ).request
    store.set_dispatch_enabled("project", True)
    dispatch = store.reserve_dispatch(request.id)

    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    issuer = secrets / "issuer.pk8"
    issuer.write_bytes(
        bytes.fromhex(
            "3051020101300506032b657004220420c226c22f628685cd349518c28eff015f"
            "d216a106bb49534286dceed3202b1c0e81210028d8b71d122a31cfd39f2631"
            "3275119934a021918f5d37d100ad2f27acbaf776"
        )
    )
    issuer.chmod(0o600)
    keyring = secrets / "evidence-keys.json"
    create_keyring(keyring, key_id="fixture-key", key=b"e" * 32)

    programs = tmp_path / "programs"
    programs.mkdir(mode=0o700)
    observation = _executable(
        programs / "observation",
        "from gov_webui.generation_boundaries import observation_main\n"
        "raise SystemExit(observation_main())\n",
    )
    standing = _executable(
        programs / "standing",
        "from gov_webui.generation_boundaries import standing_main\n"
        "raise SystemExit(standing_main())\n",
    )
    docket_standing = _executable(
        programs / "docket-standing",
        "from gov_webui.generation_boundaries import docket_standing_main\n"
        "raise SystemExit(docket_standing_main())\n",
    )
    executor = _executable(
        programs / "executor",
        "import json, sys\n"
        "from pathlib import Path\n"
        "from gov_webui.evidence_store import EncryptedEvidenceStore\n"
        "from gov_webui.generation_executor import ExecutorPlan, GenerationExecutor, "
        "parse_docket_dispatch\n"
        "op, config = sys.argv[1:]\n"
        "plan = ExecutorPlan.from_file(Path(config))\n"
        "if op == 'plan-id':\n"
        " print(plan.identity)\n"
        " raise SystemExit(0)\n"
        "dispatch = parse_docket_dispatch(sys.stdin.buffer.read())\n"
        "evidence = EncryptedEvidenceStore(plan.evidence_root, plan.evidence_keyring)\n"
        "runner = GenerationExecutor(plan, provider=lambda payload: "
        "{'content': 'qualified fixture response', 'request': payload}, "
        "evidence_writer=evidence.write)\n"
        "outcome = runner.execute(dispatch) if op == 'execute' else runner.reconcile(dispatch)\n"
        "print(json.dumps(outcome.to_dict(), sort_keys=True, separators=(',', ':')))\n",
    )
    config = WorkerConfig(
        contexts_root=contexts,
        ag_loopctl=Path(AG_LOOPCTL),
        docket=Path(DOCKET),
        observation_resolver=observation,
        standing_resolver=standing,
        docket_standing_resolver=docket_standing,
        executor=executor,
        issuer_key=issuer,
        evidence_keyring=keyring,
    )
    governed = GovernedGeneration(config, store, request)

    governed.prepare(dispatch)
    assert governed.drive() == "settled_observation_required"
    assert store.get_request(request.id).status is LogicalStatus.CANDIDATE
    assert GovernedGeneration(config, store, request).drive() == "settled_observation_required"
    assert len(store.list_dispatches(request.id)) == 1
    assert len(list(governed.logs_dir.glob("*-*.command.json"))) >= 8
