# SPDX-License-Identifier: Apache-2.0
"""Durable ag-loopctl worker program-counter and checkpoint tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gov_webui.generation_boundaries import ag_digest
from gov_webui.evidence_store import EncryptedEvidenceStore, create_keyring
from gov_webui.generation_store import GenerationStore, LogicalStatus
from gov_webui.generation_worker import (
    GenerationWorkerError,
    GovernedGeneration,
    WorkerConfig,
    process_one,
    ring_ed25519_public_key,
    run_once,
)


def test_program_counter_is_read_from_the_single_authoritative_variant() -> None:
    assert (
        GovernedGeneration._program_counter(
            {"current": {"state": {"authorization_consumed": {"meta": {}}}}}
        )
        == "authorization_consumed"
    )


def test_paused_queued_work_is_retained_without_worker_error_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contexts = tmp_path / "contexts"
    store = GenerationStore(contexts / "ctx" / "marginalia" / "generation.sqlite")
    request = store.create_request(
        client_request_id="paused",
        project_id="project",
        session_id="session",
        expected_revision=0,
        canon_fingerprint="canon",
        guidance_fingerprint="guidance",
        original_model="model",
        original_route="route",
        request={"context_id": "ctx", "messages": [], "model": "model"},
    ).request
    keyring = tmp_path / "keys.json"
    create_keyring(keyring, key_id="test", key=b"k" * 32)
    config = WorkerConfig(
        contexts_root=contexts,
        ag_loopctl=tmp_path / "ag",
        docket=tmp_path / "docket",
        observation_resolver=tmp_path / "observation",
        standing_resolver=tmp_path / "standing",
        docket_standing_resolver=tmp_path / "docket-standing",
        executor=tmp_path / "executor",
        issuer_key=tmp_path / "issuer",
        evidence_keyring=keyring,
    )
    attempted = []
    monkeypatch.setattr(
        "gov_webui.generation_worker.process_one",
        lambda *_args: attempted.append(True),
    )

    assert run_once(config) == []
    assert attempted == []
    assert store.get_request(request.id).status is LogicalStatus.QUEUED


def test_ring_pkcs8_key_requires_matching_embedded_public_key() -> None:
    document = bytes.fromhex(
        "3051020101300506032b657004220420c226c22f628685cd349518c28eff015f"
        "d216a106bb49534286dceed3202b1c0e81210028d8b71d122a31cfd39f2631"
        "3275119934a021918f5d37d100ad2f27acbaf776"
    )
    assert ring_ed25519_public_key(document).hex() == (
        "28d8b71d122a31cfd39f26313275119934a021918f5d37d100ad2f27acbaf776"
    )
    with pytest.raises(GenerationWorkerError, match="does not match"):
        ring_ed25519_public_key(document[:-1] + bytes([document[-1] ^ 1]))


def test_prepare_writes_exact_occurrence_bound_plan_and_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    contexts = tmp_path / "contexts"
    context = contexts / "ctx-a"
    store = GenerationStore(context / "marginalia" / "generation.sqlite")
    request = store.create_request(
        client_request_id="client",
        project_id="project",
        session_id="session",
        expected_revision=0,
        canon_fingerprint=ag_digest("test", "canon"),
        guidance_fingerprint=ag_digest("test", "guidance"),
        original_model="model",
        original_route="route",
        request={"context_id": "ctx-a", "messages": [], "model": "model"},
    ).request
    store.set_dispatch_enabled("project", True)
    dispatch = store.reserve_dispatch(request.id)
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    files = {}
    for name in (
        "ag-loopctl",
        "docket",
        "observation",
        "standing",
        "docket-standing",
        "executor",
        "providerctl",
        "providerctl-config",
        "model-config",
    ):
        path = deployment / name
        path.write_text("fixture", encoding="utf-8")
        files[name] = path
    keyring = deployment / "keys.json"
    keyring.write_text("fixture", encoding="utf-8")
    issuer = deployment / "issuer.pk8"
    issuer.write_text("fixture", encoding="utf-8")
    config = WorkerConfig(
        contexts_root=contexts,
        ag_loopctl=files["ag-loopctl"],
        docket=files["docket"],
        observation_resolver=files["observation"],
        standing_resolver=files["standing"],
        docket_standing_resolver=files["docket-standing"],
        executor=files["executor"],
        providerctl=files["providerctl"],
        providerctl_config=files["providerctl-config"],
        model_config=files["model-config"],
        issuer_key=issuer,
        evidence_keyring=keyring,
    )
    governed = GovernedGeneration(config, store, request, dispatch)
    monkeypatch.setattr(governed, "_trust_config", lambda: {"issuers": []})

    def fake_run(*arguments):
        if arguments[0] == "seal-runtime-profile":
            governed.runtime_profile.write_text("{}", encoding="utf-8")
        elif arguments[0] == "init":
            governed.database.write_text("fixture", encoding="utf-8")
        return {}

    monkeypatch.setattr(governed, "_run", fake_run)
    governed.prepare()

    plan = json.loads(governed.executor_plan_path.read_text())
    catalog = json.loads((governed.config_dir / "catalog.json").read_text())
    assert plan["marginalia_dispatch_id"] == dispatch.id
    assert plan["request_digest"] == dispatch.request_digest
    assert plan["schema"] == "marginalia.generation-executor-plan/v2"
    assert plan["providerctl"] == str(files["providerctl"])
    assert (
        catalog["entries"]["marginalia.generation-dispatch/v1"]["observation_basis"]["requirement"][
            "basis_identity"
        ]
        == request.request_digest
    )


def test_restart_after_dispatch_marker_uses_recover_not_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    config = WorkerConfig(
        contexts_root=tmp_path,
        ag_loopctl=tmp_path / "ag",
        docket=tmp_path / "docket",
        observation_resolver=tmp_path / "observation",
        standing_resolver=tmp_path / "standing",
        docket_standing_resolver=tmp_path / "docket-standing",
        executor=tmp_path / "executor",
        issuer_key=tmp_path / "issuer",
        evidence_keyring=tmp_path / "keys",
    )
    store = GenerationStore(tmp_path / "ctx" / "marginalia" / "generation.sqlite")
    request = store.create_request(
        client_request_id="client",
        project_id="project",
        session_id="session",
        expected_revision=0,
        canon_fingerprint="canon",
        guidance_fingerprint="guidance",
        original_model="model",
        original_route="route",
        request={"context_id": "ctx", "messages": [], "model": "model"},
    ).request
    store.set_dispatch_enabled("project", True)
    dispatch = store.reserve_dispatch(request.id)
    governed = GovernedGeneration(config, store, request, dispatch)
    governed.state_dir.mkdir(parents=True)
    governed._mark_dispatch_started()
    operations = []
    states = iter(["authorization_consumed", "settled_observation_required"])

    def fake_run(*arguments):
        operations.append(arguments[0])
        if arguments[0] == "inspect":
            state = next(states)
            return {"current": {"state": {state: {}}}}
        return {}

    monkeypatch.setattr(governed, "_run", fake_run)
    assert governed.drive() == "settled_observation_required"
    assert operations == ["inspect", "recover", "inspect"]


def test_restart_continues_immutable_command_log_sequence(tmp_path: Path) -> None:
    config = WorkerConfig(
        contexts_root=tmp_path,
        ag_loopctl=tmp_path / "ag",
        docket=tmp_path / "docket",
        observation_resolver=tmp_path / "observation",
        standing_resolver=tmp_path / "standing",
        docket_standing_resolver=tmp_path / "docket-standing",
        executor=tmp_path / "executor",
        issuer_key=tmp_path / "issuer",
        evidence_keyring=tmp_path / "keys",
    )
    store = GenerationStore(tmp_path / "ctx" / "marginalia" / "generation.sqlite")
    request = store.create_request(
        client_request_id="client",
        project_id="project",
        session_id="session",
        expected_revision=0,
        canon_fingerprint="canon",
        guidance_fingerprint="guidance",
        original_model="model",
        original_route="route",
        request={"context_id": "ctx", "messages": [], "model": "model"},
    ).request
    store.set_dispatch_enabled("project", True)
    dispatch = store.reserve_dispatch(request.id)
    first = GovernedGeneration(config, store, request, dispatch)
    first.logs_dir.mkdir(parents=True)
    (first.logs_dir / "0007-inspect.command.json").write_text("{}", encoding="utf-8")

    restarted = GovernedGeneration(config, store, request, dispatch)

    assert restarted._sequence == 7


def test_indeterminate_reconciliation_projects_unknown_without_redispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contexts = tmp_path / "contexts"
    store = GenerationStore(contexts / "ctx" / "marginalia" / "generation.sqlite")
    request = store.create_request(
        client_request_id="unknown",
        project_id="project",
        session_id="session",
        expected_revision=0,
        canon_fingerprint="canon",
        guidance_fingerprint="guidance",
        original_model="model",
        original_route="route",
        request={"context_id": "ctx", "messages": [], "model": "model"},
    ).request
    store.set_dispatch_enabled("project", True)
    dispatch = store.reserve_dispatch(request.id)
    store.mark_executing(dispatch.id, "attempt")
    config = WorkerConfig(
        contexts_root=contexts,
        ag_loopctl=tmp_path / "ag",
        docket=tmp_path / "docket",
        observation_resolver=tmp_path / "observation",
        standing_resolver=tmp_path / "standing",
        docket_standing_resolver=tmp_path / "docket-standing",
        executor=tmp_path / "executor",
        issuer_key=tmp_path / "issuer",
        evidence_keyring=tmp_path / "keys",
    )
    monkeypatch.setattr(GovernedGeneration, "prepare", lambda self: None)
    monkeypatch.setattr(GovernedGeneration, "drive", lambda self: "reconciliation_required")

    assert process_one(config, store, request) == "reconciliation_required"
    assert store.get_request(request.id).status is LogicalStatus.UNKNOWN
    assert len(store.list_dispatches(request.id)) == 1


def test_worker_purges_expired_live_evidence(tmp_path: Path) -> None:
    contexts = tmp_path / "contexts"
    evidence_keyring = tmp_path / "secrets" / "keys.json"
    create_keyring(evidence_keyring, key_id="active", key=b"k" * 32)
    store = GenerationStore(contexts / "ctx" / "marginalia" / "generation.sqlite")
    evidence = EncryptedEvidenceStore(
        store.path.parent / "generation-evidence", evidence_keyring, retention_days=1
    )
    created = evidence.write(
        {"content": "expired"},
        logical_request_id="logical",
        dispatch_id="dispatch",
        docket_attempt="attempt",
        now=datetime.now(timezone.utc) - timedelta(days=2),
    )
    evidence_id = created.reference.rsplit(":", 1)[1]
    config = WorkerConfig(
        contexts_root=contexts,
        ag_loopctl=tmp_path / "ag",
        docket=tmp_path / "docket",
        observation_resolver=tmp_path / "observation",
        standing_resolver=tmp_path / "standing",
        docket_standing_resolver=tmp_path / "docket-standing",
        executor=tmp_path / "executor",
        issuer_key=tmp_path / "issuer",
        evidence_keyring=evidence_keyring,
        retention_days=1,
    )

    assert run_once(config) == [{"evidence_id": evidence_id, "state": "evidence_purged"}]
    assert not (evidence.blobs / f"{evidence_id}.json").exists()
