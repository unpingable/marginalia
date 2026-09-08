# SPDX-License-Identifier: Apache-2.0
"""Durable ag-ng generation for application-owned, non-narrative artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from gov_webui.evidence_store import EncryptedEvidenceStore
from gov_webui.generation_outcome import (
    AuthoredGeneration,
    BlockedGeneration,
    InvalidGenerationResult,
    classify_daemon_result,
)
from gov_webui.generation_store import (
    GenerationDisabled,
    GenerationStore,
    IdempotencyConflict,
    LogicalStatus,
)
from gov_webui.project_state import canon_fingerprint, guidance_fingerprint, project_state_lock
from gov_webui.session_store import SessionStore


class InternalGenerationError(RuntimeError):
    """An internal artifact could not obtain an applicable provider candidate."""


class InternalGenerationPending(InternalGenerationError):
    """Custody remains active or indeterminate; another execution is not authorized."""


@dataclass(frozen=True)
class InternalGenerationResult:
    content: str
    usage: dict[str, int]
    candidate_id: str
    provider_id: str
    model_id: str


def _identity(
    purpose: str,
    project_id: str,
    session_id: str,
    revision: int,
    model: str,
    messages: list[dict[str, str]],
) -> str:
    material = json.dumps(
        {
            "schema": "marginalia.internal-generation-identity/v1",
            "purpose": purpose,
            "project_id": project_id,
            "session_id": session_id,
            "revision": revision,
            "model": model,
            "messages": messages,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"internal-{purpose}-" + hashlib.sha256(material).hexdigest()


async def generate_internal(
    *,
    purpose: Literal["context-maintenance", "synthetic"],
    project_id: str,
    context_id: str,
    context_root: Path,
    session_id: str,
    session_store: SessionStore,
    generation_store: GenerationStore,
    evidence_store: EncryptedEvidenceStore,
    messages: list[dict[str, str]],
    configured_model: str,
    provider_id: str,
    timeout_seconds: float = 1_830,
    poll_seconds: float = 0.5,
) -> InternalGenerationResult:
    """Queue exact work once, then reconcile its durable candidate.

    A local wait expiring never changes custody and never makes redispatch safe.
    Calling this function again with the same frozen inputs reconnects to the
    same logical request.
    """
    session = session_store.get(session_id)
    if session is None:
        raise InternalGenerationError("internal generation session does not exist")
    client_id = _identity(
        purpose,
        project_id,
        session_id,
        session.revision,
        configured_model,
        messages,
    )
    with project_state_lock(context_root):
        current = session_store.get(session_id)
        if current is None or current.revision != session.revision:
            raise InternalGenerationError("internal generation source revision changed")
        canon = canon_fingerprint(context_root)
        guidance = guidance_fingerprint(context_root)
        existing = generation_store.get_by_client_id(project_id, client_id)
        if existing is None:
            if not generation_store.dispatch_enabled(project_id):
                raise InternalGenerationError("new dispatches are disabled for this project")
            try:
                created = generation_store.create_request(
                    client_request_id=client_id,
                    project_id=project_id,
                    session_id=session_id,
                    purpose=purpose,
                    expected_revision=session.revision,
                    canon_fingerprint=canon,
                    guidance_fingerprint=guidance,
                    original_model=configured_model,
                    original_route=provider_id,
                    request={
                        "context_id": context_id,
                        "messages": messages,
                        "model": configured_model,
                    },
                )
            except (GenerationDisabled, IdempotencyConflict) as exc:
                raise InternalGenerationError(str(exc)) from exc
            logical = created.request
            try:
                generation_store.reserve_dispatch(logical.id)
            except GenerationDisabled as exc:
                if created.created:
                    generation_store.block_undispatched(logical.id, str(exc))
                raise InternalGenerationError(str(exc)) from exc
        else:
            logical = existing

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        logical = generation_store.get_request(logical.id)
        if logical is None:
            raise InternalGenerationError("internal generation custody disappeared")
        if logical.status in {LogicalStatus.CANDIDATE, LogicalStatus.ACCEPTED}:
            candidate_id = logical.candidate_id or logical.accepted_candidate_id
            if candidate_id is None:
                raise InternalGenerationError("candidate state omitted its identity")
            candidate = generation_store.get_candidate(candidate_id)
            dispatch = generation_store.get_dispatch(candidate.dispatch_id) if candidate else None
            if candidate is None or dispatch is None:
                raise InternalGenerationError("candidate custody record is incomplete")
            with project_state_lock(context_root):
                # Historical acceptance wins over present-day applicability.
                # A crash after the artifact was stored and marked consumed may
                # be followed by legitimate source/canon edits; recovery must
                # report the existing acceptance rather than trying to block it.
                if logical.status is not LogicalStatus.ACCEPTED:
                    current = session_store.get(session_id)
                    if current is None or current.revision != logical.expected_revision:
                        generation_store.block_candidate(candidate_id, "source revision changed")
                        raise InternalGenerationError("internal generation source revision changed")
                    if canon_fingerprint(context_root) != logical.canon_fingerprint:
                        generation_store.block_candidate(candidate_id, "accepted canon changed")
                        raise InternalGenerationError("accepted canon changed")
                    if guidance_fingerprint(context_root) != logical.guidance_fingerprint:
                        generation_store.block_candidate(candidate_id, "project guidance changed")
                        raise InternalGenerationError("project guidance changed")
                response = evidence_store.read_reference(
                    candidate.evidence_ref, actor=f"{purpose}-consumer"
                )
            try:
                outcome = classify_daemon_result(response, dispatch.actual_model)
            except InvalidGenerationResult as exc:
                generation_store.block_candidate(candidate_id, "provider result was invalid")
                raise InternalGenerationError("provider result was invalid") from exc
            if isinstance(outcome, BlockedGeneration):
                generation_store.block_candidate(candidate_id, "provider result requires review")
                raise InternalGenerationError("provider result requires review")
            assert isinstance(outcome, AuthoredGeneration)
            return InternalGenerationResult(
                content=outcome.content,
                usage=outcome.usage,
                candidate_id=candidate_id,
                provider_id=dispatch.actual_route,
                model_id=dispatch.actual_model,
            )
        if logical.status is LogicalStatus.UNKNOWN:
            raise InternalGenerationPending(
                "provider outcome is unknown; custody remains active and redispatch is unsafe"
            )
        if logical.status in {LogicalStatus.BLOCKED, LogicalStatus.FAILED}:
            raise InternalGenerationError(logical.last_error or f"generation {logical.status}")
        if asyncio.get_running_loop().time() >= deadline:
            raise InternalGenerationPending(
                "local wait ended while durable generation remains in progress"
            )
        await asyncio.sleep(poll_seconds)


def accept_internal_results(
    generation_store: GenerationStore,
    candidate_ids: list[str],
    *,
    artifact_id: str,
) -> None:
    """Record consumption only after the derived artifact is crash-safely stored."""
    for candidate_id in dict.fromkeys(candidate_ids):
        if generation_store.get_candidate(candidate_id) is not None:
            generation_store.accept_candidate(candidate_id, artifact_id)
