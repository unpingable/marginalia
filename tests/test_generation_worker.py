# SPDX-License-Identifier: Apache-2.0
"""Durable ag-loopctl worker program-counter and checkpoint tests."""

from __future__ import annotations

import json
import subprocess
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
    refresh_provider_readiness,
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
    store.set_dispatch_enabled("project", False)
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


def _worker_config(tmp_path: Path, contexts: Path, **overrides) -> WorkerConfig:
    values = {
        "contexts_root": contexts,
        "ag_loopctl": tmp_path / "ag",
        "docket": tmp_path / "docket",
        "observation_resolver": tmp_path / "observation",
        "standing_resolver": tmp_path / "standing",
        "docket_standing_resolver": tmp_path / "docket-standing",
        "executor": tmp_path / "executor",
        "issuer_key": tmp_path / "issuer",
        "evidence_keyring": tmp_path / "keys",
    }
    values.update(overrides)
    return WorkerConfig(**values)


def _unknown_request(store: GenerationStore):
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
    store.mark_executing(dispatch.id, "provider-attempt")
    store.mark_unknown(dispatch.id, "provider outcome unavailable")
    return store.get_request(request.id)


def test_unknown_reconcile_backoff_defers_then_redrives_with_growing_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contexts = tmp_path / "contexts"
    store = GenerationStore(contexts / "ctx" / "marginalia" / "generation.sqlite")
    request = _unknown_request(store)
    config = _worker_config(
        tmp_path, contexts, reconcile_base_seconds=5.0, reconcile_max_seconds=300.0
    )
    drives = []
    monkeypatch.setattr(
        "gov_webui.generation_worker.process_one",
        lambda *_args: drives.append(True) or "reconciliation_required",
    )

    first = run_once(config)
    assert first == [{"request_id": request.id, "state": "reconciliation_required"}]
    scheduled = store.get_request(request.id)
    assert scheduled.reconcile_attempts == 1
    deferred_until = datetime.fromisoformat(scheduled.next_reconcile_at)
    assert timedelta(0) < deferred_until - datetime.now(timezone.utc) <= timedelta(seconds=5)

    # The persisted instant defers the next drive silently.
    assert run_once(config) == []
    assert len(drives) == 1

    # Once the instant passes, the request is driven again and the delay grows.
    store.schedule_reconciliation(
        request.id,
        next_reconcile_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        reconcile_attempts=1,
    )
    assert run_once(config) == [{"request_id": request.id, "state": "reconciliation_required"}]
    rescheduled = store.get_request(request.id)
    assert rescheduled.reconcile_attempts == 2
    remaining = datetime.fromisoformat(rescheduled.next_reconcile_at) - datetime.now(timezone.utc)
    assert timedelta(seconds=5) < remaining <= timedelta(seconds=10)
    assert len(drives) == 2


def test_unknown_backoff_is_cleared_when_a_request_leaves_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contexts = tmp_path / "contexts"
    store = GenerationStore(contexts / "ctx" / "marginalia" / "generation.sqlite")
    request = _unknown_request(store)
    store.schedule_reconciliation(
        request.id,
        next_reconcile_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        reconcile_attempts=4,
    )
    config = _worker_config(tmp_path, contexts)

    def settle(config_arg, store_arg, request_arg):
        dispatch = store_arg.list_dispatches(request_arg.id)[0]
        store_arg.settle_unknown_dispatch(
            request_arg.id,
            dispatch.id,
            "operator-settled unknown dispatch; ground: operator_attested_no_send",
            disposition="operator_settlement",
            ground="operator_attested_no_send",
        )
        return "completed"

    monkeypatch.setattr("gov_webui.generation_worker.process_one", settle)

    assert run_once(config) == [{"request_id": request.id, "state": "completed"}]
    settled = store.get_request(request.id)
    assert settled.status is LogicalStatus.FAILED
    assert settled.next_reconcile_at is None
    assert settled.reconcile_attempts == 0


def test_command_log_dedupes_identical_triples_and_prunes_to_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _worker_config(tmp_path, tmp_path, command_log_retention=2)
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
    governed.logs_dir.mkdir(parents=True)
    outputs = []

    def fake_run(command, **kwargs):
        payload = outputs[-1]
        return subprocess.CompletedProcess(command, 0, payload, b"")

    monkeypatch.setattr("gov_webui.generation_worker.subprocess.run", fake_run)

    outputs.append(b'{"current":{"state":{"a":{}}}}')
    governed._run("inspect", "--database", "db")
    governed._run("inspect", "--database", "db")
    assert sorted(path.name for path in governed.logs_dir.iterdir()) == [
        "0001-inspect.command.json",
        "0001-inspect.stderr",
        "0001-inspect.stdout",
    ]

    for index in range(3):
        outputs.append(f'{{"current":{{"state":{{"v{index}":{{}}}}}}}}'.encode())
        governed._run("inspect", "--database", "db")

    names = sorted(path.name for path in governed.logs_dir.iterdir())
    assert names == [
        "0004-inspect.command.json",
        "0004-inspect.stderr",
        "0004-inspect.stdout",
        "0005-inspect.command.json",
        "0005-inspect.stderr",
        "0005-inspect.stdout",
    ]


def test_run_enforces_the_configured_command_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _worker_config(tmp_path, tmp_path, command_timeout_seconds=7.0)
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
    governed.logs_dir.mkdir(parents=True)
    seen = {}

    def fake_run(command, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr("gov_webui.generation_worker.subprocess.run", fake_run)

    with pytest.raises(GenerationWorkerError, match="command bound"):
        governed._run("inspect", "--database", "db")
    assert seen["timeout"] == 7.0
    assert list(governed.logs_dir.iterdir()) == []


def test_refresh_provider_readiness_writes_atomically_and_keeps_prior_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_config = tmp_path / "providers.json"
    model_config.write_text(
        json.dumps(
            {
                "version": 1,
                "default_model": "writer",
                "providers": [
                    {
                        "id": "openai-api",
                        "protocol": "openai-compatible",
                        "base_url": "https://provider.invalid/v1",
                        "api_key_env": "FIXTURE_KEY",
                        "models": [{"id": "writer", "model": "upstream", "label": "Writer"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    readiness_path = tmp_path / "shared" / "provider-readiness.json"
    config = _worker_config(
        tmp_path,
        tmp_path,
        providerctl=tmp_path / "ag-providerctl",
        providerctl_config=tmp_path / "providerctl.toml",
        model_config=model_config,
        readiness_path=readiness_path,
    )

    class FakeGateway:
        endpoints = {"openai-api": "credential_unavailable"}
        failure = None

        def __init__(self, *_args, **_kwargs):
            pass

        def endpoint_readiness(self):
            if self.failure is not None:
                raise self.failure
            return dict(self.endpoints)

    monkeypatch.setattr("gov_webui.generation_worker.AgProviderGateway", FakeGateway)

    result = refresh_provider_readiness(config)
    assert result == {"state": "provider_readiness_refreshed", "endpoints": 1}
    document = json.loads(readiness_path.read_text(encoding="utf-8"))
    assert document["schema"] == "marginalia.provider-readiness/v1"
    assert document["endpoints"] == {"openai-api": "credential_unavailable"}
    assert document["refreshed_at"]

    FakeGateway.failure = GenerationWorkerError("providerd unreachable")
    with pytest.raises(GenerationWorkerError):
        refresh_provider_readiness(config)
    assert json.loads(readiness_path.read_text(encoding="utf-8")) == document


def test_worker_config_reads_reconcile_command_and_readiness_knobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARGINALIA_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MARGINALIA_GENERATION_RECONCILE_BASE_SECONDS", "3")
    monkeypatch.setenv("MARGINALIA_GENERATION_RECONCILE_MAX_SECONDS", "120")
    monkeypatch.setenv("MARGINALIA_GENERATION_COMMAND_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("MARGINALIA_GENERATION_COMMAND_LOG_RETENTION", "25")
    monkeypatch.setenv("MARGINALIA_PROVIDER_READINESS_INTERVAL_SECONDS", "45")

    config = WorkerConfig.from_environment()

    assert config.reconcile_base_seconds == 3.0
    assert config.reconcile_max_seconds == 120.0
    assert config.command_timeout_seconds == 60.0
    assert config.command_log_retention == 25
    assert config.readiness_interval_seconds == 45.0
    assert config.readiness_path == tmp_path / ".marginalia" / "shared" / "provider-readiness.json"

    monkeypatch.setenv("MARGINALIA_GENERATION_RECONCILE_MAX_SECONDS", "1")
    with pytest.raises(GenerationWorkerError, match="invalid"):
        WorkerConfig.from_environment()
