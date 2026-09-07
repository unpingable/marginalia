# SPDX-License-Identifier: Apache-2.0
"""Docket executor idempotency and crash-boundary tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gov_webui.generation_executor import (
    DocketDispatch,
    EvidenceReference,
    ExecutorAttemptStore,
    ExecutorError,
    ExecutorPlan,
    GenerationExecutor,
)
from gov_webui.generation_executor_cli import run
from gov_webui.generation_store import GenerationStore, LogicalStatus


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def fixture(tmp_path: Path):
    generation_path = tmp_path / "generation.sqlite"
    store = GenerationStore(generation_path)
    request = store.create_request(
        client_request_id="client-1",
        project_id="project-a",
        session_id="session-a",
        expected_revision=0,
        canon_fingerprint=digest("canon"),
        guidance_fingerprint=digest("guidance"),
        original_model="model-a",
        original_route="route-a",
        request={"messages": [{"role": "user", "content": "Write"}]},
    ).request
    store.set_dispatch_enabled("project-a", True)
    reserved = store.reserve_dispatch(request.id)
    plan = ExecutorPlan(
        attempt_store=tmp_path / "attempts.sqlite",
        generation_store=generation_path,
        evidence_root=tmp_path / "evidence",
        evidence_keyring=tmp_path / "keys.json",
        evidence_retention_days=30,
        marginalia_dispatch_id=reserved.id,
        request_digest=reserved.request_digest,
        subject=digest("subject"),
        scope=digest("scope"),
    )
    dispatch = DocketDispatch(
        attempt=digest("attempt"),
        marker=digest("marker"),
        work_schema="marginalia.generation-dispatch/v1",
        work=plan.identity,
        subject=plan.subject,
        scope=plan.scope,
    )
    return store, request, plan, dispatch


def test_execute_runs_provider_once_and_exact_replay_returns_same_outcome(tmp_path: Path) -> None:
    store, request, plan, dispatch = fixture(tmp_path)
    calls = 0

    def provider(payload):
        nonlocal calls
        calls += 1
        assert payload["messages"][0]["content"] == "Write"
        return {"outcome": "authored", "content": "Result"}

    def evidence(response, **_identity):
        assert response["content"] == "Result"
        return EvidenceReference("evidence:key-1:one", digest("response"))

    executor = GenerationExecutor(plan, provider=provider, evidence_writer=evidence)
    first = executor.execute(dispatch)
    second = executor.execute(dispatch)

    assert first == second
    assert first.outcome == "success"
    assert calls == 1
    assert store.get_request(request.id).status is LogicalStatus.CANDIDATE


def test_crash_after_mechanics_boundary_cannot_repeat_provider(tmp_path: Path) -> None:
    _store, _request, plan, dispatch = fixture(tmp_path)
    attempts = ExecutorAttemptStore(plan.attempt_store)
    assert attempts.reserve(dispatch) is None
    attempts.begin(dispatch)
    calls = 0

    def provider(_payload):
        nonlocal calls
        calls += 1
        return {}

    executor = GenerationExecutor(
        plan,
        provider=provider,
        evidence_writer=lambda *_args, **_kwargs: EvidenceReference("unused", digest("unused")),
    )
    with pytest.raises(ExecutorError, match="already crossed"):
        executor.execute(dispatch)
    assert calls == 0
    reconciled = executor.reconcile(dispatch)
    assert reconciled.outcome == "indeterminate"


def test_timeout_is_indeterminate_and_reconcile_never_calls_provider(tmp_path: Path) -> None:
    store, request, plan, dispatch = fixture(tmp_path)
    calls = 0

    def provider(_payload):
        nonlocal calls
        calls += 1
        raise TimeoutError("provider deadline ended without lookup evidence")

    executor = GenerationExecutor(
        plan,
        provider=provider,
        evidence_writer=lambda *_args, **_kwargs: pytest.fail("no response exists"),
    )
    result = executor.execute(dispatch)
    assert result.outcome == "indeterminate"
    assert store.get_request(request.id).status is LogicalStatus.UNKNOWN

    reconciled = executor.reconcile(dispatch)
    assert reconciled == result
    assert calls == 1


def test_substituted_dispatch_is_refused_without_mechanics(tmp_path: Path) -> None:
    _store, _request, plan, dispatch = fixture(tmp_path)
    calls = 0

    def provider(_payload):
        nonlocal calls
        calls += 1
        return {}

    executor = GenerationExecutor(
        plan,
        provider=provider,
        evidence_writer=lambda *_args, **_kwargs: EvidenceReference("unused", digest("unused")),
    )
    substituted = DocketDispatch(
        attempt=dispatch.attempt,
        marker=dispatch.marker,
        work_schema=dispatch.work_schema,
        work=dispatch.work,
        subject=digest("different-subject"),
        scope=dispatch.scope,
    )
    with pytest.raises(ExecutorError, match="subject or scope"):
        executor.execute(substituted)
    assert calls == 0


def test_plan_file_round_trips_and_plan_id_operation_is_exact(tmp_path: Path) -> None:
    _store, _request, plan, _dispatch = fixture(tmp_path)
    path = tmp_path / "executor-plan.json"
    path.write_text(json.dumps(plan.canonical_value()), encoding="utf-8")

    loaded = ExecutorPlan.from_file(path)
    assert loaded == plan
    assert run(["plan-id", str(path)]) == plan.identity
