# SPDX-License-Identifier: Apache-2.0
"""Operator workflow for prebuilding and activating bounded fiction context."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any

from gov_webui.context_budget import (
    TiktokenCounter,
    as_messages,
    choose_summary_prefix,
    maintenance_lookahead_tokens,
)
from gov_webui.context_maintenance import ContextMaintainer, SummaryModelResult
from gov_webui.context_summary import (
    ContextSummaryError,
    ContextSummaryStore,
)
from gov_webui.daemon_client import DaemonChatClient, default_socket_path
from gov_webui.generation_outcome import (
    AuthoredGeneration,
    BlockedGeneration,
    classify_daemon_result,
)
from gov_webui.governed_chat_adapter import GovernedChatAdapter
from gov_webui.library_store import LibraryStore, ProjectRecord
from gov_webui.model_providers import ConfiguredModel, load_provider_catalog
from gov_webui.session_store import ChatSession, SessionStore


def _safe_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


class ContextOperations:
    """Resumable, non-narrative context bootstrap over durable sessions."""

    def __init__(
        self,
        *,
        data_root: Path,
        default_context_id: str,
        model_config: Path | None,
        maintenance_model: str,
        socket_path: Path | None = None,
    ) -> None:
        self.data_root = data_root
        self.context_base = data_root / ".governor"
        self.library = LibraryStore(
            data_root / "marginalia" / "library.json",
            default_context_id=default_context_id,
        )
        self.model_config = model_config
        self.maintenance_model_id = maintenance_model
        self.socket_path = socket_path or default_socket_path(self.context_base)

    def _maintenance_model(self) -> ConfiguredModel:
        if self.model_config is None:
            raise ContextSummaryError(
                "context build requires --model-config or MARGINALIA_MODEL_CONFIG"
            )
        model = load_provider_catalog(self.model_config).require_available(
            self.maintenance_model_id
        )
        if model.purpose != "context-maintenance":
            raise ContextSummaryError(
                "configured context-maintenance model is not marked for context maintenance"
            )
        return model

    def _projects(
        self,
        *,
        workspace_id: str | None,
        project_id: str | None,
    ) -> list[ProjectRecord]:
        state = self.library.snapshot()
        projects = list(state.projects.values())
        if workspace_id is not None:
            projects = [item for item in projects if item.workspace_id == workspace_id]
        if project_id is not None:
            projects = [item for item in projects if item.id == project_id]
        if not projects:
            raise ContextSummaryError("no matching projects were found")
        return projects

    def _sessions(
        self,
        project: ProjectRecord,
        *,
        session_id: str | None,
    ) -> list[ChatSession]:
        lifecycle = self.library.snapshot().conversations
        allowed = {item.session_id for item in lifecycle.values() if item.project_id == project.id}
        store = SessionStore(self.context_base / project.context_id / "sessions")
        sessions = []
        for item in store.list_summaries():
            if item["id"] not in allowed:
                continue
            if session_id is not None and item["id"] != session_id:
                continue
            session = store.get(item["id"])
            if session is not None:
                sessions.append(session)
        return sessions

    def _store(self, project: ProjectRecord) -> ContextSummaryStore:
        return ContextSummaryStore(self.context_base / project.context_id)

    def _counter(self, policy: Any) -> TiktokenCounter:
        return TiktokenCounter(policy.tokenizer_encoding, policy.token_safety_multiplier)

    def plan(
        self,
        *,
        workspace_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        reports = []
        ready = True
        for project in self._projects(workspace_id=workspace_id, project_id=project_id):
            summary_store = self._store(project)
            policy = summary_store.policy()
            counter = self._counter(policy)
            for session in self._sessions(project, session_id=session_id):
                history_tokens = counter.count_messages(as_messages(session.messages))
                if not policy.enabled:
                    reports.append(
                        {
                            "project_id": project.id,
                            "session_ref": _safe_ref(session.id),
                            "revision": session.revision,
                            "message_count": len(session.messages),
                            "history_tokens": history_tokens,
                            "needs_summary": False,
                            "summary_valid": False,
                            "summary_ready": True,
                            "covered_messages": 0,
                            "required_covered_messages": 0,
                            "error": None,
                        }
                    )
                    continue
                threshold = int(
                    (policy.application_tokens - maintenance_lookahead_tokens(policy))
                    * policy.maintenance_watermark
                )
                needs_summary = False
                valid_summary = False
                summary_ready = False
                covered = 0
                required_covered = 0
                error = None
                try:
                    summary = summary_store.load(session)
                    if summary is not None:
                        valid_summary = True
                        covered = len(summary.source.covered_message_ids)
                except ContextSummaryError as exc:
                    error = str(exc)
                try:
                    if history_tokens >= threshold:
                        required_covered = len(
                            choose_summary_prefix(
                                session,
                                [],
                                "Continue the story.",
                                policy,
                                counter,
                                additional_reserve_tokens=maintenance_lookahead_tokens(policy),
                            )
                        )
                except ContextSummaryError as exc:
                    error = str(exc) if error is None else error
                needs_summary = required_covered > 0
                summary_ready = error is None and (
                    not needs_summary or (valid_summary and covered >= required_covered)
                )
                if not summary_ready:
                    ready = False
                reports.append(
                    {
                        "project_id": project.id,
                        "session_ref": _safe_ref(session.id),
                        "revision": session.revision,
                        "message_count": len(session.messages),
                        "history_tokens": history_tokens,
                        "needs_summary": needs_summary,
                        "summary_valid": valid_summary,
                        "summary_ready": summary_ready,
                        "covered_messages": covered,
                        "required_covered_messages": required_covered,
                        "error": error,
                    }
                )
        return {
            "ready": ready,
            "maintenance_model": self.maintenance_model_id,
            "sessions": reports,
        }

    async def _generate(
        self,
        adapter: GovernedChatAdapter,
        messages: list[dict[str, str]],
        configured_model: str,
        maintenance_model: ConfiguredModel,
    ) -> SummaryModelResult:
        raw = await adapter.chat_send(messages=messages, model=configured_model)
        outcome = classify_daemon_result(raw, configured_model)
        if isinstance(outcome, BlockedGeneration):
            raise ContextSummaryError("context-maintenance output was blocked")
        if not isinstance(outcome, AuthoredGeneration) or outcome.model != configured_model:
            raise ContextSummaryError("context-maintenance model returned an invalid result")
        return SummaryModelResult(
            content=outcome.content,
            usage=outcome.usage,
            receipt_id=outcome.receipt["receipt_id"],
            provider_id=maintenance_model.provider_id,
            model_id=maintenance_model.model_id,
        )

    async def build(
        self,
        *,
        workspace_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        reports = []
        maintenance_model = self._maintenance_model()
        for project in self._projects(workspace_id=workspace_id, project_id=project_id):
            store = self._store(project)
            policy = store.policy()
            counter = self._counter(policy)
            maintenance_id = f"{project.context_id[:116]}-maintenance"
            adapter = GovernedChatAdapter(
                DaemonChatClient(str(self.socket_path)),
                context_id=maintenance_id,
                expected_governor_dir=str(self.context_base),
            )
            try:
                for session in self._sessions(project, session_id=session_id):
                    history_tokens = counter.count_messages(as_messages(session.messages))
                    threshold = int(
                        (policy.application_tokens - maintenance_lookahead_tokens(policy))
                        * policy.maintenance_watermark
                    )
                    if history_tokens < threshold:
                        reports.append(
                            {
                                "session_ref": _safe_ref(session.id),
                                "status": "not_needed",
                                "history_tokens": history_tokens,
                            }
                        )
                        continue
                    try:
                        existing = store.load(session)
                    except ContextSummaryError:
                        existing = None
                    source = choose_summary_prefix(
                        session,
                        [],
                        "Continue the story.",
                        policy,
                        counter,
                        additional_reserve_tokens=maintenance_lookahead_tokens(policy),
                    )
                    if not source:
                        reports.append(
                            {
                                "session_ref": _safe_ref(session.id),
                                "status": "not_needed",
                                "history_tokens": history_tokens,
                            }
                        )
                        continue
                    if existing is not None and len(existing.source.covered_message_ids) >= len(
                        source
                    ):
                        reports.append(
                            {
                                "session_ref": _safe_ref(session.id),
                                "status": "already_valid",
                                "covered_messages": len(existing.source.covered_message_ids),
                            }
                        )
                        continue

                    async def generate(
                        messages: list[dict[str, str]],
                        configured_model: str,
                    ) -> SummaryModelResult:
                        return await self._generate(
                            adapter,
                            messages,
                            configured_model,
                            maintenance_model,
                        )

                    maintainer = ContextMaintainer(
                        store=store,
                        policy=policy,
                        counter=counter,
                        configured_model=maintenance_model.id,
                        provider_id=maintenance_model.provider_id,
                        model_id=maintenance_model.model_id,
                        generate=generate,
                    )
                    summary = await maintainer.maintain(session, source)
                    reports.append(
                        {
                            "session_ref": _safe_ref(session.id),
                            "status": "built",
                            "covered_messages": len(summary.source.covered_message_ids),
                            "source_sha256": summary.source.prefix_sha256,
                        }
                    )
            finally:
                await adapter.close()
        return {"ready": True, "sessions": reports}

    def validate(
        self,
        *,
        workspace_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return self.plan(
            workspace_id=workspace_id,
            project_id=project_id,
            session_id=session_id,
        )

    def activate(
        self,
        *,
        enabled: bool,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        projects = self._projects(workspace_id=workspace_id, project_id=project_id)
        if enabled:
            validation = self.validate(workspace_id=workspace_id, project_id=project_id)
            if not validation["ready"]:
                raise ContextSummaryError(
                    "bounded context cannot activate until required summaries validate"
                )
        states = []
        for project in projects:
            policy = self._store(project).set_enabled(enabled)
            states.append({"project_id": project.id, "enabled": policy.enabled})
        return {"ready": True, "projects": states}


def run_build(operations: ContextOperations, **filters: Any) -> dict[str, Any]:
    return asyncio.run(operations.build(**filters))
