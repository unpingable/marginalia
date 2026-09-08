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
    ProviderOutcomeUnknown,
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
        request={
            "context_id": "context-a",
            "messages": [{"role": "user", "content": "Write"}],
            "model": "model-a",
        },
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
        assert payload["model"] == "model-a"
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


class FakeDurableProvider:
    def __init__(self, response: dict | None = None) -> None:
        self.dispatch = digest("provider-dispatch")
        self.exact = digest("provider-event-stream")
        self.response = response or {
            "outcome": "authored",
            "content": "Durably retained",
            "provider_evidence": {
                "dispatch": self.dispatch,
                "exact_event_stream": self.exact,
                "event_stream": "fixture",
            },
        }
        self.execute_calls = 0
        self.fetch_calls = 0
        self.ack_calls = 0
        self.fail_execute = False
        self.fail_first_ack = False

    def prepare(self, payload, **identity):
        assert payload["model"] == "model-a"
        assert identity["actual_route"] == "route-a"
        return {"schema": "fixture", "dispatch": self.dispatch}

    def execute(self, transaction, *, selected_model):
        self.execute_calls += 1
        assert transaction["dispatch"] == self.dispatch
        assert selected_model == "model-a"
        if self.fail_execute:
            raise ProviderOutcomeUnknown("browser wait ended")
        return self.response

    def fetch(self, dispatch, *, selected_model):
        self.fetch_calls += 1
        assert dispatch == self.dispatch
        assert selected_model == "model-a"
        return self.response

    def acknowledge(self, dispatch, exact_event_stream, custody):
        self.ack_calls += 1
        assert (dispatch, exact_event_stream) == (self.dispatch, self.exact)
        assert custody == digest("response")
        if self.fail_first_ack and self.ack_calls == 1:
            raise ProviderOutcomeUnknown("acknowledgment lost")


def _durable_executor(tmp_path: Path, provider: FakeDurableProvider):
    store, request, plan, dispatch = fixture(tmp_path)

    def evidence(response, **_identity):
        assert response["content"] == "Durably retained"
        return EvidenceReference("evidence:key-1:durable", digest("response"))

    return (
        store,
        request,
        dispatch,
        GenerationExecutor(plan, provider=provider, evidence_writer=evidence),
    )


def test_durable_timeout_reconciles_fetched_response_without_redispatch(tmp_path: Path) -> None:
    provider = FakeDurableProvider()
    provider.fail_execute = True
    store, request, dispatch, executor = _durable_executor(tmp_path, provider)

    first = executor.execute(dispatch)
    assert first.outcome == "indeterminate"
    assert store.get_request(request.id).status is LogicalStatus.UNKNOWN

    recovered = executor.reconcile(dispatch)
    assert recovered.outcome == "success"
    assert provider.execute_calls == 1
    assert provider.fetch_calls == 1
    assert provider.ack_calls == 1
    assert store.get_request(request.id).status is LogicalStatus.CANDIDATE


def test_lost_ack_after_candidate_reconciles_existing_candidate(tmp_path: Path) -> None:
    provider = FakeDurableProvider()
    provider.fail_first_ack = True
    store, request, dispatch, executor = _durable_executor(tmp_path, provider)

    first = executor.execute(dispatch)
    assert first.outcome == "indeterminate"
    assert store.get_request(request.id).status is LogicalStatus.CANDIDATE

    recovered = executor.reconcile(dispatch)
    assert recovered.outcome == "success"
    assert provider.execute_calls == 1
    assert provider.fetch_calls == 0
    assert provider.ack_calls == 2
    assert len(store.list_dispatches(request.id)) == 1


def test_prepare_failure_is_definitive_before_provider_boundary(tmp_path: Path) -> None:
    provider = FakeDurableProvider()

    def refuse(*_args, **_kwargs):
        raise ExecutorError("route policy refused")

    provider.prepare = refuse
    store, request, dispatch, executor = _durable_executor(tmp_path, provider)

    result = executor.execute(dispatch)
    assert result.outcome == "failure"
    assert provider.execute_calls == 0
    assert store.get_request(request.id).status is LogicalStatus.FAILED
