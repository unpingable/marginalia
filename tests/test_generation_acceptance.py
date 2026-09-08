# SPDX-License-Identifier: Apache-2.0
"""Atomic candidate acceptance and post-insertion recovery tests."""

from __future__ import annotations

from pathlib import Path

from gov_webui.evidence_store import EncryptedEvidenceStore, create_keyring
from gov_webui.generation_acceptance import AcceptanceStatus, accept_candidate
from gov_webui.generation_store import DispatchStatus, GenerationStore, LogicalStatus
from gov_webui.project_state import canon_fingerprint, guidance_fingerprint
from gov_webui.session_store import CandidateWriteResult, SessionMessage, SessionStore


from gov_webui.writer_continuity import Anchor, AnchorType, Severity, create_registry


def _candidate(
    tmp_path: Path,
    *,
    response_content: str = "The door opened.",
    forbidden_pattern: str | None = None,
):
    context = tmp_path / "context"
    if forbidden_pattern is not None:
        registry = create_registry(context / ".governor")
        registry.register(
            Anchor(
                id="forbid-sigil",
                anchor_type=AnchorType.PROHIBITION,
                description="The forbidden sigil must not appear.",
                forbidden_patterns=[forbidden_pattern],
                severity=Severity.REJECT,
            )
        )
        registry.save(context / ".governor" / "continuity" / "anchors.json")
    sessions = SessionStore(context / "sessions")
    session = sessions.create("ctx", model="model")
    generations = GenerationStore(context / "marginalia" / "generation.sqlite")
    created = generations.create_request(
        client_request_id="client-request",
        project_id="project",
        session_id=session.id,
        expected_revision=session.revision,
        canon_fingerprint=canon_fingerprint(context),
        guidance_fingerprint=guidance_fingerprint(context),
        original_model="model",
        original_route="provider",
        request={
            "context_id": "ctx",
            "messages": [{"role": "user", "content": "Continue the scene."}],
            "model": "model",
        },
    ).request
    generations.set_dispatch_enabled("project", True)
    dispatch = generations.reserve_dispatch(created.id)
    generations.mark_executing(dispatch.id, "provider-attempt")
    keyring = tmp_path / "secrets" / "keys.json"
    create_keyring(keyring, key_id="key", key=b"k" * 32)
    evidence = EncryptedEvidenceStore(context / "marginalia" / "evidence", keyring)
    reference = evidence.write(
        {
            "content": response_content,
            "model": "model",
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            "receipt": {"receipt_id": "receipt-1"},
        },
        logical_request_id=created.id,
        dispatch_id=dispatch.id,
        docket_attempt="docket-attempt",
    )
    candidate = generations.record_candidate(
        dispatch.id,
        response_digest=reference.response_digest,
        evidence_ref=reference.reference,
    )
    return context, sessions, session, generations, evidence, candidate


def test_accepts_candidate_once_and_persists_candidate_identity(tmp_path: Path) -> None:
    context, sessions, session, generations, evidence, candidate = _candidate(tmp_path)
    result = accept_candidate(
        generation_store=generations,
        evidence_store=evidence,
        session_store=sessions,
        context_root=context,
        project_id="project",
        candidate_id=candidate.id,
    )

    assert result.status is AcceptanceStatus.ACCEPTED
    durable = sessions.get(session.id)
    assert durable is not None
    assert [item.content for item in durable.messages] == [
        "Continue the scene.",
        "The door opened.",
    ]
    assert durable.messages[-1].generation_candidate_id == candidate.id
    assert generations.get_request(candidate.logical_request_id).status is LogicalStatus.ACCEPTED


def test_prior_insertion_wins_before_changed_canon_check(tmp_path: Path) -> None:
    context, sessions, session, generations, evidence, candidate = _candidate(tmp_path)
    assistant = SessionMessage.create(
        role="assistant", content="The door opened.", generation_candidate_id=candidate.id
    )
    write, message_id = sessions.append_candidate_if_revision(
        session.id,
        session.revision,
        candidate.id,
        [SessionMessage.create(role="user", content="Continue the scene."), assistant],
    )
    assert write is CandidateWriteResult.COMMITTED
    assert message_id == assistant.id
    canon = context / ".governor" / "continuity" / "anchors.json"
    canon.parent.mkdir(parents=True)
    canon.write_text('{"changed":true}\n', encoding="utf-8")

    recovered = accept_candidate(
        generation_store=generations,
        evidence_store=evidence,
        session_store=sessions,
        context_root=context,
        project_id="project",
        candidate_id=candidate.id,
    )

    assert recovered.status is AcceptanceStatus.ALREADY_ACCEPTED
    assert recovered.message_id == assistant.id
    assert len(sessions.get(session.id).messages) == 2
    assert generations.get_candidate(candidate.id).accepted_message_id == assistant.id


def test_changed_canon_blocks_uninserted_candidate(tmp_path: Path) -> None:
    context, sessions, session, generations, evidence, candidate = _candidate(tmp_path)
    canon = context / ".governor" / "continuity" / "anchors.json"
    canon.parent.mkdir(parents=True)
    canon.write_text('{"changed":true}\n', encoding="utf-8")

    result = accept_candidate(
        generation_store=generations,
        evidence_store=evidence,
        session_store=sessions,
        context_root=context,
        project_id="project",
        candidate_id=candidate.id,
    )

    assert result.status is AcceptanceStatus.BLOCKED
    assert result.reason == "accepted canon changed"
    assert sessions.get(session.id).messages == []
    assert generations.get_request(candidate.logical_request_id).status is LogicalStatus.BLOCKED


def test_continuity_conflict_is_durable_and_writer_fix_accepts_once(tmp_path: Path) -> None:
    context, sessions, session, generations, evidence, candidate = _candidate(
        tmp_path,
        response_content="The forbidden sigil burned above the door.",
        forbidden_pattern="forbidden sigil",
    )

    blocked = accept_candidate(
        generation_store=generations,
        evidence_store=evidence,
        session_store=sessions,
        context_root=context,
        project_id="project",
        candidate_id=candidate.id,
    )

    assert blocked.status is AcceptanceStatus.BLOCKED
    assert sessions.get(session.id).messages == []
    event = generations.events(candidate.logical_request_id)[-1]
    assert event["event_type"] == "candidate_blocked"
    assert event["detail"]["conflict"]["violations"][0]["anchor_id"] == "forbid-sigil"

    accepted = accept_candidate(
        generation_store=generations,
        evidence_store=evidence,
        session_store=sessions,
        context_root=context,
        project_id="project",
        candidate_id=candidate.id,
        corrected_text="A pale light burned above the door.",
    )

    assert accepted.status is AcceptanceStatus.ACCEPTED
    durable = sessions.get(session.id)
    assert durable is not None
    assert durable.messages[-1].content == "A pale light burned above the door."
    dispatch = generations.get_dispatch(candidate.dispatch_id)
    assert dispatch is not None
    assert dispatch.status is DispatchStatus.CANDIDATE
