# SPDX-License-Identifier: Apache-2.0
"""Exact ag-ng and Docket generation-boundary response tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from gov_webui.generation_boundaries import (
    BoundaryRefusal,
    OBSERVATION_BASIS_TYPE,
    OBSERVATION_RESOLVER_ID,
    ag_digest,
    generation_scope,
    generation_subject,
    observation_resolution,
    standing_resolution,
)
from gov_webui.generation_store import GenerationStore


def request(tmp_path: Path, monkeypatch):
    contexts = tmp_path / "contexts"
    path = contexts / "ctx-a" / "marginalia" / "generation.sqlite"
    store = GenerationStore(path)
    created = store.create_request(
        client_request_id="client-1",
        project_id="project-a",
        session_id="session-a",
        expected_revision=4,
        canon_fingerprint="sha256:canon",
        guidance_fingerprint="sha256:guidance",
        original_model="model-a",
        original_route="route-a",
        request={"context_id": "ctx-a", "messages": [], "model": "model-a"},
    ).request
    store.set_dispatch_enabled("project-a", True)
    store.reserve_dispatch(created.id)
    monkeypatch.setenv("GOVERNOR_CONTEXTS_DIR", str(contexts))
    return created


def test_observation_binds_exact_application_basis(tmp_path: Path, monkeypatch) -> None:
    created = request(tmp_path, monkeypatch)
    value = {
        "schema": "ag.governed-loop.observation-request/v1",
        "key": {"campaign": ag_digest("test", "campaign"), "occurrence": "one"},
        "observation": created.request_digest,
        "subject": generation_subject(created),
        "now_unix_ms": 1000,
    }
    result = observation_resolution(value)
    basis = result["basis"]
    assert basis["basis_type"] == OBSERVATION_BASIS_TYPE
    assert basis["basis_identity"] == created.request_digest
    assert result["resolver_id"] == OBSERVATION_RESOLVER_ID
    assert result["normalized_preconditions"] == ag_digest(
        "ag.governed-loop.typed-observation-basis/v1", basis
    )


def test_standing_refuses_substituted_scope(tmp_path: Path, monkeypatch) -> None:
    created = request(tmp_path, monkeypatch)
    value = {
        "schema": "ag.governed-loop.standing-request/v1",
        "key": {"campaign": ag_digest("test", "campaign"), "occurrence": "one"},
        "observation": created.request_digest,
        "proposal": ag_digest("test", "proposal"),
        "subject": generation_subject(created),
        "scope": ag_digest("test", "substituted"),
        "now_unix_ms": 1000,
    }
    with pytest.raises(BoundaryRefusal, match="subject/scope"):
        standing_resolution(value)

    value["scope"] = generation_scope(created)
    assert standing_resolution(value)["status"] == "current"
