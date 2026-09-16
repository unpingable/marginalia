# SPDX-License-Identifier: Apache-2.0
"""Startup migration checks and scheduled-backup regressions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from gov_webui.artifact_store import ArtifactStore
from gov_webui.backup_worker import run_once
from gov_webui.library_store import LibraryStore
from gov_webui.ops import migration_preflight
from gov_webui.state_layout import contexts_root, shared_root


def _write_v1_library(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_project_id": "default",
                "projects": {
                    "default": {
                        "id": "default",
                        "name": "Existing novel",
                        "context_id": "erin-writing",
                        "archived": False,
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                },
                "conversations": {},
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_preflight_requires_explicit_supported_migration(tmp_path):
    library_path = shared_root(tmp_path) / "library.json"
    _write_v1_library(library_path)

    blocked = migration_preflight(
        data_root=tmp_path,
        default_context_id="erin-writing",
        apply_migrations=False,
    )
    applied = migration_preflight(
        data_root=tmp_path,
        default_context_id="erin-writing",
        apply_migrations=True,
    )

    assert blocked["ready"] is False
    assert blocked["migration_required"] is True
    assert json.loads(library_path.read_text())["schema_version"] == 2
    assert applied["ready"] is True
    assert applied["migration_applied"] is True
    assert applied["workspaces"] == 1


def test_preflight_rejects_a_future_schema_without_rewriting_it(tmp_path):
    path = shared_root(tmp_path) / "library.json"
    path.parent.mkdir(parents=True)
    original = '{"schema_version":99}\n'
    path.write_text(original)

    result = migration_preflight(
        data_root=tmp_path,
        default_context_id="erin-writing",
        apply_migrations=True,
    )

    assert result["ready"] is False
    assert "newer" in result["errors"][0]
    assert path.read_text() == original


def test_daily_worker_runs_once_per_utc_day_and_manual_policy_never_runs(tmp_path):
    data_root = tmp_path / "data"
    backup_root = tmp_path / "backups"
    library = LibraryStore(
        shared_root(data_root) / "library.json",
        default_context_id="erin-writing",
    )
    library.update_workspace(
        "erin",
        backup_enabled=True,
        backup_schedule="daily",
        backup_hour_utc=4,
    )
    now = datetime(2026, 8, 20, 5, tzinfo=timezone.utc)

    first = run_once(
        data_root=data_root,
        backup_root=backup_root,
        default_context_id="erin-writing",
        now=now,
    )
    duplicate = run_once(
        data_root=data_root,
        backup_root=backup_root,
        default_context_id="erin-writing",
        now=now,
    )
    library.update_workspace("erin", backup_schedule="manual")
    manual = run_once(
        data_root=data_root,
        backup_root=backup_root,
        default_context_id="erin-writing",
        now=datetime(2026, 8, 21, 5, tzinfo=timezone.utc),
    )

    assert len(first) == 1
    assert first[0]["ok"] is True
    assert duplicate == []
    assert manual == []


def test_preflight_checks_artifact_content_hashes(tmp_path):
    LibraryStore(
        shared_root(tmp_path) / "library.json",
        default_context_id="erin-writing",
    )
    context = contexts_root(tmp_path) / "erin-writing"
    artifacts = ArtifactStore(context / ".governor")
    artifact, _, _ = artifacts.create(
        title="Exact draft",
        content="This content is hash-bound.",
        kind="markdown",
        language="",
    )
    healthy = migration_preflight(
        data_root=tmp_path,
        default_context_id="erin-writing",
    )

    content_path = (
        context / ".governor" / ".governor" / "artifacts" / "content" / artifact.id / "v1.txt"
    )
    content_path.write_text("tampered")
    damaged = migration_preflight(
        data_root=tmp_path,
        default_context_id="erin-writing",
    )

    assert healthy["ready"] is True
    assert damaged["ready"] is False
    assert any("artifact content hash mismatch" in item for item in damaged["errors"])


class _FakeGateway:
    def __init__(self, *, outcome: Exception | dict | None = None) -> None:
        self.outcome = outcome
        self.fetches: list[tuple[str, str]] = []

    def fetch(self, dispatch: str, *, selected_model: str):
        self.fetches.append((dispatch, selected_model))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _unknown_custody(data_root: Path):
    from gov_webui.generation_store import GenerationStore

    store = GenerationStore(
        contexts_root(data_root) / "erin-writing" / "marginalia" / "generation.sqlite"
    )
    request = store.create_request(
        client_request_id="stuck",
        project_id="project-a",
        session_id="session-a",
        expected_revision=0,
        canon_fingerprint="sha256:canon",
        guidance_fingerprint="sha256:guidance",
        original_model="writer-primary",
        original_route="provider-primary",
        request={"messages": [{"role": "user", "content": "Write"}]},
    ).request
    store.set_dispatch_enabled("project-a", True)
    dispatch = store.reserve_dispatch(request.id)
    provider_dispatch = "sha256:" + "a" * 64
    store.mark_executing(dispatch.id, provider_dispatch)
    store.mark_unknown(dispatch.id, "browser wait ended; provider outcome unavailable")
    return store, store.get_request(request.id), store.get_dispatch(dispatch.id), provider_dispatch


def test_settle_unknown_dispatch_dry_run_verifies_but_writes_nothing(tmp_path: Path) -> None:
    from gov_webui.generation_executor import ProviderOutcomeUnknown
    from gov_webui.ops import settle_unknown_dispatch

    data_root = tmp_path / "data"
    store, request, dispatch, provider_dispatch = _unknown_custody(data_root)
    gateway = _FakeGateway(outcome=ProviderOutcomeUnknown("provider custody is unresolved"))

    result = settle_unknown_dispatch(
        data_root=data_root,
        request_id=request.id,
        dispatch_id=dispatch.id,
        expected_updated_at=request.updated_at,
        ground="providerd_refused_before_send",
        gateway=gateway,
    )

    assert result["ready"] is True
    assert result["applied"] is False
    assert result["provider_dispatch"] == provider_dispatch
    assert result["live_provider_phase"] == "ProviderOutcomeUnknown"
    assert gateway.fetches == [(provider_dispatch, "writer-primary")]
    assert store.get_request(request.id).status.value == "unknown"
    assert store.get_dispatch(dispatch.id).status.value == "unknown"


def test_settle_unknown_dispatch_apply_is_terminal_and_replay_refused(tmp_path: Path) -> None:
    from gov_webui.generation_executor import ProviderOutcomeUnknown
    from gov_webui.ops import settle_unknown_dispatch

    data_root = tmp_path / "data"
    store, request, dispatch, _ = _unknown_custody(data_root)
    gateway = _FakeGateway(outcome=ProviderOutcomeUnknown("provider custody is unresolved"))

    applied = settle_unknown_dispatch(
        data_root=data_root,
        request_id=request.id,
        dispatch_id=dispatch.id,
        expected_updated_at=request.updated_at,
        ground="providerd_refused_before_send",
        apply=True,
        gateway=gateway,
    )

    assert applied["ready"] is True
    assert applied["applied"] is True
    settled = store.get_request(request.id)
    assert settled.status.value == "failed"
    assert settled.failure_type == "provider_unavailable"
    event = store.events(request.id)[-1]
    assert event["event_type"] == "dispatch_failed"
    assert event["detail"]["disposition"] == "operator_settlement"
    assert event["detail"]["ground"] == "providerd_refused_before_send"
    assert event["detail"]["failure_type"] == "provider_unavailable"

    replayed = settle_unknown_dispatch(
        data_root=data_root,
        request_id=request.id,
        dispatch_id=dispatch.id,
        expected_updated_at=request.updated_at,
        ground="providerd_refused_before_send",
        apply=True,
        gateway=gateway,
    )
    assert replayed["ready"] is False
    assert replayed["applied"] is False


def test_settle_unknown_dispatch_refuses_inconsistent_or_held_outcomes(
    tmp_path: Path,
) -> None:
    from gov_webui.generation_executor import (
        ProviderDefinitiveFailure,
        ProviderOutcomeUnknown,
        ProviderRefusedBeforeSend,
    )
    from gov_webui.ops import settle_unknown_dispatch

    data_root = tmp_path / "data"
    store, request, dispatch, _ = _unknown_custody(data_root)

    # The connect-class ground requires providerd to hold connect-class evidence.
    inconsistent = settle_unknown_dispatch(
        data_root=data_root,
        request_id=request.id,
        dispatch_id=dispatch.id,
        expected_updated_at=request.updated_at,
        ground="providerd_transport_connect_predispatch",
        gateway=_FakeGateway(outcome=ProviderOutcomeUnknown("unresolved: body")),
    )
    assert inconsistent["ready"] is False

    consistent = settle_unknown_dispatch(
        data_root=data_root,
        request_id=request.id,
        dispatch_id=dispatch.id,
        expected_updated_at=request.updated_at,
        ground="providerd_transport_connect_predispatch",
        gateway=_FakeGateway(
            outcome=ProviderRefusedBeforeSend("provider transport failed before send: connect")
        ),
    )
    assert consistent["ready"] is True
    assert consistent["applied"] is False

    # A completed response held by providerd is candidate-grade custody.
    held = settle_unknown_dispatch(
        data_root=data_root,
        request_id=request.id,
        dispatch_id=dispatch.id,
        expected_updated_at=request.updated_at,
        ground="operator_attested_no_send",
        gateway=_FakeGateway(outcome={"outcome": "authored", "content": "Held response"}),
    )
    assert held["ready"] is False
    assert "completed response" in held["errors"][0]

    terminal = settle_unknown_dispatch(
        data_root=data_root,
        request_id=request.id,
        dispatch_id=dispatch.id,
        expected_updated_at=request.updated_at,
        ground="operator_attested_no_send",
        gateway=_FakeGateway(outcome=ProviderDefinitiveFailure("provider returned status 400")),
    )
    assert terminal["ready"] is False

    stale = settle_unknown_dispatch(
        data_root=data_root,
        request_id=request.id,
        dispatch_id=dispatch.id,
        expected_updated_at="2026-01-01T00:00:00+00:00",
        ground="operator_attested_no_send",
        gateway=_FakeGateway(outcome=ProviderOutcomeUnknown("unresolved")),
    )
    assert stale["ready"] is False

    missing = settle_unknown_dispatch(
        data_root=data_root,
        request_id="gen_missing",
        dispatch_id=dispatch.id,
        expected_updated_at=request.updated_at,
        ground="operator_attested_no_send",
        gateway=_FakeGateway(outcome=ProviderOutcomeUnknown("unresolved")),
    )
    assert missing["ready"] is False
    assert store.get_request(request.id).status.value == "unknown"


def test_settle_unknown_dispatch_refuses_a_candidate_holding_dispatch(tmp_path: Path) -> None:
    from gov_webui.generation_executor import ProviderOutcomeUnknown
    from gov_webui.ops import settle_unknown_dispatch

    data_root = tmp_path / "data"
    store, request, dispatch, _ = _unknown_custody(data_root)
    store.record_candidate(
        dispatch.id,
        response_digest="sha256:response",
        evidence_ref="evidence:key-1:blob-1",
    )

    result = settle_unknown_dispatch(
        data_root=data_root,
        request_id=request.id,
        dispatch_id=dispatch.id,
        expected_updated_at=store.get_request(request.id).updated_at,
        ground="operator_attested_no_send",
        gateway=_FakeGateway(outcome=ProviderOutcomeUnknown("unresolved")),
    )

    assert result["ready"] is False
    assert any("candidate" in error for error in result["errors"])
