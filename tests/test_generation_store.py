# SPDX-License-Identifier: Apache-2.0
"""Durable logical-request, dispatch, and candidate identity tests."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from gov_webui.generation_store import (
    DispatchStatus,
    GenerationDisabled,
    GENERATION_POLICY_SEMANTICS,
    LEGACY_GENERATION_POLICY_DISPOSITION,
    GenerationSettingsVersionConflict,
    GenerationStore,
    GenerationTransitionError,
    IdempotencyConflict,
    LogicalStatus,
)


def create(store: GenerationStore, *, client_id: str = "client-1", content: str = "Write"):
    return store.create_request(
        client_request_id=client_id,
        project_id="project-a",
        session_id="session-a",
        expected_revision=7,
        canon_fingerprint="sha256:canon",
        guidance_fingerprint="sha256:guidance",
        original_model="writer-primary",
        original_route="provider-primary",
        fallback_policy=[
            {"model": "writer-fallback", "route": "provider-fallback"},
        ],
        request={"messages": [{"role": "user", "content": content}], "temperature": 0.7},
    )


def test_client_id_is_idempotent_only_for_the_exact_frozen_request(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    first = create(store)
    duplicate = create(store)

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.request.id == first.request.id
    assert duplicate.request.request_digest == first.request.request_digest

    with pytest.raises(IdempotencyConflict):
        create(store, content="Different work")

    with pytest.raises(ValueError, match="estimated_prompt_tokens"):
        store.create_request(
            client_request_id="bad-estimate",
            project_id="project-a",
            session_id="session-a",
            expected_revision=0,
            canon_fingerprint="sha256:canon",
            guidance_fingerprint="sha256:guidance",
            original_model="writer",
            original_route="provider",
            estimated_prompt_tokens=-1,
            request={"messages": []},
        )


def test_dispatch_has_immutable_actual_route_and_distinct_identity(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request = create(store).request
    store.set_dispatch_enabled("project-a", True)

    dispatch = store.reserve_dispatch(request.id)

    assert dispatch.actual_model == "writer-primary"
    assert dispatch.actual_route == "provider-primary"
    assert dispatch.request_digest != request.request_digest
    assert store.get_request(request.id).status is LogicalStatus.DISPATCHING

    with pytest.raises(GenerationTransitionError):
        store.reserve_dispatch(request.id)


def test_kill_switch_stops_new_dispatch_but_preserves_inspection(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request = create(store).request
    store.set_dispatch_enabled("project-a", False)

    with pytest.raises(GenerationDisabled):
        store.reserve_dispatch(request.id)
    assert store.get_request(request.id) == request
    assert store.events(request.id)[0]["event_type"] == "request_created"


def test_legacy_disabled_preference_migrates_to_enabled_generation(tmp_path: Path) -> None:
    path = tmp_path / "generation.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE generation_settings(
                   project_id TEXT PRIMARY KEY,
                   dispatch_enabled INTEGER NOT NULL,
                   fallback_policy_json TEXT NOT NULL DEFAULT '[]',
                   updated_at TEXT NOT NULL
               )"""
        )
        connection.execute(
            "INSERT INTO generation_settings VALUES(?, ?, ?, ?)",
            ("project-a", 0, "[]", "2026-09-08T07:31:52+00:00"),
        )

    settings = GenerationStore(path).settings("project-a")

    assert settings.dispatch_enabled is True
    assert settings.version == 2
    assert settings.policy_semantics == GENERATION_POLICY_SEMANTICS
    assert settings.migration_disposition == LEGACY_GENERATION_POLICY_DISPOSITION
    assert settings.migrated_at is not None


def test_generation_setting_update_is_revision_checked(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    initial = store.settings("project-a")
    assert initial.dispatch_enabled is True
    assert initial.version == 0

    paused = store.set_settings(
        "project-a", dispatch_enabled=False, expected_version=initial.version
    )
    assert paused.dispatch_enabled is False
    assert paused.version == 1

    with pytest.raises(GenerationSettingsVersionConflict) as conflict:
        store.set_settings("project-a", dispatch_enabled=True, expected_version=0)
    assert conflict.value.current_version == 1
    assert store.settings("project-a") == paused


def test_new_undispatched_work_can_be_settled_without_inventing_a_dispatch(
    tmp_path: Path,
) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request = create(store).request

    store.block_undispatched(request.id, "generation paused before dispatch")

    settled = store.get_request(request.id)
    assert settled is not None
    assert settled.status is LogicalStatus.BLOCKED
    assert settled.last_error == "generation paused before dispatch"
    assert store.list_dispatches(request.id) == []
    assert store.events(request.id)[-1]["event_type"] == "request_blocked_before_dispatch"


def test_unknown_dispatch_is_not_safe_to_retry(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request = create(store).request
    store.set_dispatch_enabled("project-a", True)
    dispatch = store.reserve_dispatch(request.id)
    store.mark_executing(dispatch.id, "provider-operation-1")
    store.mark_unknown(dispatch.id, "browser wait ended; provider outcome unavailable")

    assert store.get_dispatch(dispatch.id).status is DispatchStatus.UNKNOWN
    assert store.get_request(request.id).status is LogicalStatus.UNKNOWN
    store.set_dispatch_enabled("project-a", False)
    assert store.get_dispatch(dispatch.id).provider_execution_id == "provider-operation-1"
    store.set_dispatch_enabled("project-a", True)
    with pytest.raises(GenerationTransitionError):
        store.reserve_dispatch(request.id)


def test_fetched_response_enters_candidate_acceptance_not_retry_safety(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request = create(store).request
    store.set_dispatch_enabled("project-a", True)
    dispatch = store.reserve_dispatch(request.id)
    store.mark_executing(dispatch.id, "provider-operation-1")
    store.mark_unknown(dispatch.id, "connection lost")

    candidate = store.record_candidate(
        dispatch.id,
        response_digest="sha256:response",
        evidence_ref="evidence:key-1:blob-1",
    )
    duplicate = store.record_candidate(
        dispatch.id,
        response_digest="sha256:response",
        evidence_ref="evidence:key-1:blob-1",
    )

    assert duplicate == candidate
    assert store.get_request(request.id).status is LogicalStatus.CANDIDATE
    assert store.get_dispatch(dispatch.id).status is DispatchStatus.CANDIDATE
    with pytest.raises(GenerationTransitionError):
        store.reserve_dispatch(request.id)


def test_fallback_cannot_replace_an_indeterminate_dispatch(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request = create(store).request
    store.set_dispatch_enabled("project-a", True)
    first = store.reserve_dispatch(request.id)
    store.mark_executing(first.id)
    store.mark_unknown(first.id, "no provider lookup facility")

    with pytest.raises(GenerationTransitionError):
        store.reserve_fallback(request.id)


def test_qualified_failure_can_use_only_the_next_frozen_fallback(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request = create(store).request
    store.set_dispatch_enabled("project-a", True)
    first = store.reserve_dispatch(request.id)
    store.mark_executing(first.id)
    store.mark_failed(first.id, "qualified provider refusal")

    fallback = store.reserve_fallback(request.id)

    assert fallback.ordinal == 1
    assert (fallback.actual_model, fallback.actual_route) == (
        "writer-fallback",
        "provider-fallback",
    )
    assert store.dispatch_payload(fallback.id)["model"] == "writer-fallback"
    assert [item["event_type"] for item in store.events(request.id)][-1] == "fallback_reserved"


def test_fallback_is_stopped_by_kill_switch_and_exhaustion(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request = create(store).request
    store.set_dispatch_enabled("project-a", True)
    first = store.reserve_dispatch(request.id)
    store.mark_failed(first.id, "qualified provider refusal")
    store.set_dispatch_enabled("project-a", False)
    with pytest.raises(GenerationDisabled):
        store.reserve_fallback(request.id)

    store.set_dispatch_enabled("project-a", True)
    fallback = store.reserve_fallback(request.id)
    store.mark_failed(fallback.id, "qualified fallback refusal")
    with pytest.raises(GenerationTransitionError, match="exhausted"):
        store.reserve_fallback(request.id)


def unknown_dispatch(store: GenerationStore):
    request = create(store).request
    store.set_dispatch_enabled("project-a", True)
    dispatch = store.reserve_dispatch(request.id)
    store.mark_executing(dispatch.id, "provider-operation-1")
    store.mark_unknown(dispatch.id, "browser wait ended; provider outcome unavailable")
    return store.get_request(request.id), store.get_dispatch(dispatch.id)


def test_unknown_settlement_is_compare_and_set_with_typed_ground(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request, dispatch = unknown_dispatch(store)

    store.settle_unknown_dispatch(
        request.id,
        dispatch.id,
        "operator-settled unknown dispatch; ground: providerd_refused_before_send",
        failure_type="provider_unavailable",
        disposition="operator_settlement",
        ground="providerd_refused_before_send",
        expected_updated_at=request.updated_at,
    )

    settled = store.get_request(request.id)
    assert settled.status is LogicalStatus.FAILED
    assert settled.failure_type == "provider_unavailable"
    assert store.get_dispatch(dispatch.id).status is DispatchStatus.FAILED
    event = store.events(request.id)[-1]
    assert event["event_type"] == "dispatch_failed"
    assert event["dispatch_id"] == dispatch.id
    assert event["detail"] == {
        "reason": "operator-settled unknown dispatch; ground: providerd_refused_before_send",
        "failure_type": "provider_unavailable",
        "disposition": "operator_settlement",
        "ground": "providerd_refused_before_send",
    }
    # A second settlement is refused: the rows are no longer unknown.
    with pytest.raises(GenerationTransitionError):
        store.settle_unknown_dispatch(
            request.id,
            dispatch.id,
            "repeat",
            disposition="operator_settlement",
            ground="providerd_refused_before_send",
        )


def test_unknown_settlement_event_extends_mark_failed_shape_exactly(tmp_path: Path) -> None:
    failed_store = GenerationStore(tmp_path / "failed.sqlite")
    request = create(failed_store).request
    failed_store.set_dispatch_enabled("project-a", True)
    dispatch = failed_store.reserve_dispatch(request.id)
    failed_store.mark_executing(dispatch.id)
    failed_store.mark_failed(dispatch.id, "same reason", failure_type="provider_unavailable")
    mark_failed_detail = failed_store.events(request.id)[-1]["detail"]

    settled_store = GenerationStore(tmp_path / "settled.sqlite")
    settled_request, settled_dispatch = unknown_dispatch(settled_store)
    settled_store.settle_unknown_dispatch(
        settled_request.id,
        settled_dispatch.id,
        "same reason",
        failure_type="provider_unavailable",
        disposition="executor_reconcile",
        ground="providerd_transport_connect_predispatch",
    )
    settled_detail = settled_store.events(settled_request.id)[-1]["detail"]

    assert mark_failed_detail == {"reason": "same reason", "failure_type": "provider_unavailable"}
    assert settled_detail == {
        **mark_failed_detail,
        "disposition": "executor_reconcile",
        "ground": "providerd_transport_connect_predispatch",
    }


def test_unknown_settlement_refuses_stale_evidence_snapshot(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request, dispatch = unknown_dispatch(store)

    with pytest.raises(GenerationTransitionError, match="evidence snapshot"):
        store.settle_unknown_dispatch(
            request.id,
            dispatch.id,
            "stale",
            disposition="operator_settlement",
            ground="operator_attested_no_send",
            expected_updated_at="2026-01-01T00:00:00+00:00",
        )

    assert store.get_request(request.id).status is LogicalStatus.UNKNOWN
    assert store.get_dispatch(dispatch.id).status is DispatchStatus.UNKNOWN


def test_unknown_settlement_refuses_a_dispatch_holding_a_candidate(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request, dispatch = unknown_dispatch(store)
    candidate = store.record_candidate(
        dispatch.id,
        response_digest="sha256:response",
        evidence_ref="evidence:key-1:blob-1",
    )
    assert store.get_dispatch(dispatch.id).candidate_id == candidate.id
    # Simulate inconsistent custody: candidate rows exist while both rows were
    # forced back to unknown outside the store's transitions.
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE dispatch SET status='unknown' WHERE id=?", (dispatch.id,))
        connection.execute("UPDATE logical_request SET status='unknown' WHERE id=?", (request.id,))

    with pytest.raises(GenerationTransitionError, match="candidate"):
        store.settle_unknown_dispatch(
            request.id,
            dispatch.id,
            "must not settle",
            disposition="operator_settlement",
            ground="operator_attested_no_send",
        )

    assert store.get_candidate(candidate.id) is not None
    assert store.get_dispatch(dispatch.id).candidate_id == candidate.id


def test_unknown_settlement_requires_unknown_rows_and_exact_identity(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request = create(store).request
    store.set_dispatch_enabled("project-a", True)
    dispatch = store.reserve_dispatch(request.id)

    with pytest.raises(GenerationTransitionError, match="both unknown"):
        store.settle_unknown_dispatch(
            request.id,
            dispatch.id,
            "not unknown",
            disposition="operator_settlement",
            ground="operator_attested_no_send",
        )
    other = create(store, client_id="client-2").request
    with pytest.raises(GenerationTransitionError, match="does not belong"):
        store.settle_unknown_dispatch(
            other.id,
            dispatch.id,
            "wrong owner",
            disposition="operator_settlement",
            ground="operator_attested_no_send",
        )
    with pytest.raises(KeyError):
        store.settle_unknown_dispatch(
            request.id,
            "dsp_missing",
            "missing",
            disposition="operator_settlement",
            ground="operator_attested_no_send",
        )

    assert store.get_request(request.id).status is LogicalStatus.DISPATCHING
    assert store.get_dispatch(dispatch.id).status is DispatchStatus.RESERVED


def test_reconcile_backoff_schedules_and_clears_without_touching_updated_at(
    tmp_path: Path,
) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request, dispatch = unknown_dispatch(store)

    store.schedule_reconciliation(
        request.id,
        next_reconcile_at="2026-09-16T00:00:30+00:00",
        reconcile_attempts=3,
    )
    scheduled = store.get_request(request.id)
    assert scheduled.next_reconcile_at == "2026-09-16T00:00:30+00:00"
    assert scheduled.reconcile_attempts == 3
    assert scheduled.updated_at == request.updated_at

    with pytest.raises(GenerationTransitionError, match="unknown"):
        other = create(store, client_id="client-2").request
        store.schedule_reconciliation(
            other.id,
            next_reconcile_at="2026-09-16T00:00:30+00:00",
            reconcile_attempts=1,
        )

    store.clear_reconciliation_backoff(request.id)
    cleared = store.get_request(request.id)
    assert cleared.next_reconcile_at is None
    assert cleared.reconcile_attempts == 0
    assert cleared.updated_at == request.updated_at
    assert store.get_dispatch(dispatch.id).status is DispatchStatus.UNKNOWN


def test_leaving_unknown_clears_reconcile_backoff(tmp_path: Path) -> None:
    store = GenerationStore(tmp_path / "generation.sqlite")
    request, dispatch = unknown_dispatch(store)
    store.schedule_reconciliation(
        request.id,
        next_reconcile_at="2026-09-16T00:00:30+00:00",
        reconcile_attempts=2,
    )

    store.record_candidate(
        dispatch.id,
        response_digest="sha256:response",
        evidence_ref="evidence:key-1:blob-1",
    )
    assert store.get_request(request.id).next_reconcile_at is None
    assert store.get_request(request.id).reconcile_attempts == 0


def test_reconcile_backoff_columns_migrate_additively(tmp_path: Path) -> None:
    path = tmp_path / "generation.sqlite"
    with sqlite3.connect(path) as connection:
        # The schema-5 logical_request shape: every existing column except the
        # two reconcile-bookkeeping additions.
        connection.execute(
            """CREATE TABLE logical_request(
                   id TEXT PRIMARY KEY,
                   client_request_id TEXT NOT NULL,
                   project_id TEXT NOT NULL,
                   session_id TEXT NOT NULL,
                   purpose TEXT NOT NULL DEFAULT 'conversation',
                   expected_revision INTEGER NOT NULL,
                   canon_fingerprint TEXT NOT NULL,
                   guidance_fingerprint TEXT NOT NULL,
                   original_model TEXT NOT NULL,
                   original_route TEXT NOT NULL,
                   fallback_policy_json TEXT NOT NULL,
                   delivery_digest TEXT,
                   estimated_prompt_tokens INTEGER,
                   request_digest TEXT NOT NULL,
                   request_json TEXT NOT NULL,
                   status TEXT NOT NULL,
                   candidate_id TEXT,
                   accepted_candidate_id TEXT,
                   last_error TEXT,
                   failure_type TEXT,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL,
                   UNIQUE(project_id, client_request_id)
               )"""
        )
        connection.execute(
            """INSERT INTO logical_request(
                   id,client_request_id,project_id,session_id,purpose,expected_revision,
                   canon_fingerprint,guidance_fingerprint,original_model,original_route,
                   fallback_policy_json,request_digest,request_json,status,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "gen_legacy",
                "client",
                "project-a",
                "session-a",
                "conversation",
                0,
                "canon",
                "guidance",
                "model",
                "route",
                "[]",
                "digest",
                "{}",
                "unknown",
                "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
            ),
        )

    store = GenerationStore(path)

    migrated = store.get_request("gen_legacy")
    assert migrated.next_reconcile_at is None
    assert migrated.reconcile_attempts == 0
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == GenerationStore.SCHEMA_VERSION
    assert GenerationStore.SCHEMA_VERSION == 6
