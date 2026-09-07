# SPDX-License-Identifier: Apache-2.0
"""Durable logical-request, dispatch, and candidate identity tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from gov_webui.generation_store import (
    DispatchStatus,
    GenerationDisabled,
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

    with pytest.raises(GenerationDisabled):
        store.reserve_dispatch(request.id)
    assert store.get_request(request.id) == request
    assert store.events(request.id)[0]["event_type"] == "request_created"


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
