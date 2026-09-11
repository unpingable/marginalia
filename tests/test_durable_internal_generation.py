# SPDX-License-Identifier: Apache-2.0
"""Non-narrative generations share custody without sharing acceptance authority."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gov_webui.durable_internal_generation import (
    InternalGenerationDisabled,
    accept_internal_results,
    generate_internal,
)
from gov_webui.evidence_store import EncryptedEvidenceStore, create_keyring
from gov_webui.generation_acceptance import accept_candidate
from gov_webui.generation_store import GenerationStore, LogicalStatus
from gov_webui.session_store import SessionStore


@pytest.mark.asyncio
async def test_paused_internal_generation_does_not_leave_queued_custody(
    tmp_path: Path,
) -> None:
    context = tmp_path / "context"
    sessions = SessionStore(context / "sessions")
    session = sessions.create("ctx", model="summary-model")
    generations = GenerationStore(context / "marginalia" / "generation.sqlite")
    generations.set_dispatch_enabled("project", False)
    keyring = tmp_path / "keys.json"
    create_keyring(keyring, key_id="test", key=b"k" * 32)

    with pytest.raises(InternalGenerationDisabled, match="dispatches are disabled"):
        await generate_internal(
            purpose="synthetic",
            project_id="project",
            context_id="ctx",
            context_root=context,
            session_id=session.id,
            session_store=sessions,
            generation_store=generations,
            evidence_store=EncryptedEvidenceStore(context / "marginalia" / "evidence", keyring),
            messages=[{"role": "user", "content": "Probe."}],
            configured_model="summary-model",
            provider_id="provider",
        )

    assert generations.list_requests() == []


@pytest.mark.asyncio
async def test_internal_candidate_reconnects_and_cannot_enter_story(tmp_path: Path) -> None:
    context = tmp_path / "context"
    sessions = SessionStore(context / "sessions")
    session = sessions.create("ctx", model="summary-model")
    generations = GenerationStore(context / "marginalia" / "generation.sqlite")
    generations.set_dispatch_enabled("project", True)
    keyring = tmp_path / "keys.json"
    create_keyring(keyring, key_id="test", key=b"k" * 32)
    evidence = EncryptedEvidenceStore(context / "marginalia" / "evidence", keyring)
    prompt = [{"role": "user", "content": "Summarize exact source."}]

    pending = asyncio.create_task(
        generate_internal(
            purpose="context-maintenance",
            project_id="project",
            context_id="ctx",
            context_root=context,
            session_id=session.id,
            session_store=sessions,
            generation_store=generations,
            evidence_store=evidence,
            messages=prompt,
            configured_model="summary-model",
            provider_id="provider",
            timeout_seconds=2,
            poll_seconds=0.01,
        )
    )
    for _ in range(100):
        requests = generations.list_requests()
        if requests and generations.list_dispatches(requests[0].id):
            break
        await asyncio.sleep(0.01)
    logical = generations.list_requests()[0]
    assert logical.purpose == "context-maintenance"
    dispatch = generations.list_dispatches(logical.id)[0]
    generations.mark_executing(dispatch.id, "provider-attempt")
    reference = evidence.write(
        {
            "outcome": "authored",
            "content": "A source-bound summary.",
            "model": "summary-model",
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            "receipt": {"receipt_id": "ag-ng-receipt"},
        },
        logical_request_id=logical.id,
        dispatch_id=dispatch.id,
        docket_attempt="attempt",
    )
    candidate = generations.record_candidate(
        dispatch.id,
        response_digest=reference.response_digest,
        evidence_ref=reference.reference,
    )

    result = await pending
    replay = await generate_internal(
        purpose="context-maintenance",
        project_id="project",
        context_id="ctx",
        context_root=context,
        session_id=session.id,
        session_store=sessions,
        generation_store=generations,
        evidence_store=evidence,
        messages=prompt,
        configured_model="summary-model",
        provider_id="provider",
        timeout_seconds=1,
        poll_seconds=0.01,
    )
    assert replay.candidate_id == result.candidate_id == candidate.id
    assert len(generations.list_dispatches(logical.id)) == 1
    with pytest.raises(PermissionError, match="not eligible"):
        accept_candidate(
            generation_store=generations,
            evidence_store=evidence,
            session_store=sessions,
            context_root=context,
            project_id="project",
            candidate_id=candidate.id,
        )
    assert sessions.get(session.id).messages == []

    accept_internal_results(generations, [candidate.id], artifact_id="summary:stored")
    assert generations.get_request(logical.id).status is LogicalStatus.ACCEPTED

    # Historical acceptance is resolved before current applicability. A later
    # legitimate canon edit cannot relabel the already-stored artifact stale.
    canon = context / ".governor" / "continuity" / "anchors.json"
    canon.parent.mkdir(parents=True)
    canon.write_text('{"changed":true}\n', encoding="utf-8")
    accepted_replay = await generate_internal(
        purpose="context-maintenance",
        project_id="project",
        context_id="ctx",
        context_root=context,
        session_id=session.id,
        session_store=sessions,
        generation_store=generations,
        evidence_store=evidence,
        messages=prompt,
        configured_model="summary-model",
        provider_id="provider",
        timeout_seconds=1,
        poll_seconds=0.01,
    )
    assert accepted_replay.candidate_id == candidate.id
    assert generations.get_request(logical.id).status is LogicalStatus.ACCEPTED
