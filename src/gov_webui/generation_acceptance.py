# SPDX-License-Identifier: Apache-2.0
"""Application authority for revision-checked candidate insertion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from gov_webui.evidence_store import EncryptedEvidenceStore
from gov_webui.generation_outcome import (
    AuthoredGeneration,
    BlockedGeneration,
    InvalidGenerationResult,
    classify_daemon_result,
)
from gov_webui.generation_store import GenerationStore
from gov_webui.project_state import canon_fingerprint, guidance_fingerprint, project_state_lock
from gov_webui.session_store import CandidateWriteResult, SessionMessage, SessionStore


class AcceptanceStatus(StrEnum):
    ACCEPTED = "accepted"
    ALREADY_ACCEPTED = "already_accepted"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class AcceptanceResult:
    status: AcceptanceStatus
    message_id: str | None = None
    reason: str | None = None


def accept_candidate(
    *,
    generation_store: GenerationStore,
    evidence_store: EncryptedEvidenceStore,
    session_store: SessionStore,
    context_root: Path,
    project_id: str,
    candidate_id: str,
    accounting_resolver: Callable[[str, str, dict[str, int], int | None], dict[str, Any]]
    | None = None,
) -> AcceptanceResult:
    """Accept once while revision, canon, and guidance remain frozen."""
    candidate = generation_store.get_candidate(candidate_id)
    if candidate is None:
        raise KeyError(candidate_id)
    request = generation_store.get_request(candidate.logical_request_id)
    dispatch = generation_store.get_dispatch(candidate.dispatch_id)
    if request is None or dispatch is None:
        raise RuntimeError("candidate custody record is incomplete")
    if request.project_id != project_id:
        raise PermissionError("candidate belongs to another project")

    with project_state_lock(context_root):
        # This must precede current fingerprint checks. A crash after insertion
        # establishes historical acceptance even if canon legitimately changed.
        prior = session_store.candidate_message_id(request.session_id, candidate_id)
        if prior is not None:
            generation_store.accept_candidate(candidate_id, prior)
            return AcceptanceResult(AcceptanceStatus.ALREADY_ACCEPTED, message_id=prior)

        session = session_store.get(request.session_id)
        if session is None:
            return _block(generation_store, candidate_id, "target session no longer exists")
        if session.revision != request.expected_revision:
            return _block(generation_store, candidate_id, "session revision changed")
        if canon_fingerprint(context_root) != request.canon_fingerprint:
            return _block(generation_store, candidate_id, "accepted canon changed")
        if guidance_fingerprint(context_root) != request.guidance_fingerprint:
            return _block(generation_store, candidate_id, "project guidance changed")

        response = evidence_store.read_reference(
            candidate.evidence_ref, actor="generation-acceptance"
        )
        try:
            outcome = classify_daemon_result(response, dispatch.actual_model)
        except InvalidGenerationResult as exc:
            return _block(generation_store, candidate_id, f"response is not authored output: {exc}")
        if isinstance(outcome, BlockedGeneration):
            return _block(generation_store, candidate_id, "governor response requires review")
        assert isinstance(outcome, AuthoredGeneration)

        provider_request = generation_store.request_payload(request.id)
        prompts = provider_request.get("messages")
        pending_user = next(
            (
                item.get("content")
                for item in reversed(prompts if isinstance(prompts, list) else [])
                if isinstance(item, dict) and item.get("role") == "user"
            ),
            None,
        )
        if not isinstance(pending_user, str) or not pending_user.strip():
            return _block(generation_store, candidate_id, "frozen request has no user turn")
        accounting = (
            accounting_resolver(
                dispatch.actual_model,
                dispatch.actual_route,
                outcome.usage,
                request.estimated_prompt_tokens,
            )
            if accounting_resolver
            else None
        )
        messages = [
            SessionMessage.create(role="user", content=pending_user),
            SessionMessage.create(
                role="assistant",
                content=outcome.content,
                model=outcome.model,
                usage=outcome.usage,
                provider_id=dispatch.actual_route,
                model_id=(accounting or {}).get("model_id", dispatch.actual_model),
                accounting=accounting,
                generation_candidate_id=candidate_id,
            ),
        ]
        result, message_id = session_store.append_candidate_if_revision(
            request.session_id,
            request.expected_revision,
            candidate_id,
            messages,
        )
        if result is CandidateWriteResult.CONFLICT:
            return _block(generation_store, candidate_id, "session revision changed")
        if result is CandidateWriteResult.NOT_FOUND:
            return _block(generation_store, candidate_id, "target session no longer exists")
        assert message_id is not None
        generation_store.accept_candidate(candidate_id, message_id)
        status = (
            AcceptanceStatus.ALREADY_ACCEPTED
            if result is CandidateWriteResult.ALREADY_COMMITTED
            else AcceptanceStatus.ACCEPTED
        )
        return AcceptanceResult(status, message_id=message_id)


def _block(store: GenerationStore, candidate_id: str, reason: str) -> AcceptanceResult:
    store.block_candidate(candidate_id, reason)
    return AcceptanceResult(AcceptanceStatus.BLOCKED, reason=reason)
