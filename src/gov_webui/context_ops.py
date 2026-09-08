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
    effective_context_policy,
    maintenance_lookahead_tokens,
)
from gov_webui.context_maintenance import ContextMaintainer, SummaryModelResult
from gov_webui.context_summary import (
    ContextSummaryError,
    ContextSummaryStore,
    ContextTooLarge,
    CounterIdentity,
)
from gov_webui.creative_project import CreativeProjectError, CreativeProjectStore
from gov_webui.durable_internal_generation import accept_internal_results, generate_internal
from gov_webui.evidence_store import EncryptedEvidenceStore
from gov_webui.fixed_context import fiction_fixed_context_messages
from gov_webui.generation_store import GenerationStore
from gov_webui.library_store import LibraryStore, ProjectRecord
from gov_webui.model_providers import (
    ConfiguredModel,
    ProviderConfigurationError,
    load_provider_catalog,
)
from gov_webui.session_store import ChatSession, SessionStore
from gov_webui.state_layout import contexts_root, shared_root


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
        self.context_base = contexts_root(data_root)
        self.library = LibraryStore(
            shared_root(data_root) / "library.json",
            default_context_id=default_context_id,
        )
        self.model_config = model_config
        self.maintenance_model_id = maintenance_model
        # Retained as an ignored constructor parameter for CLI compatibility
        # during the state-layout transition. Context builds no longer use a
        # classic daemon socket.
        self.socket_path = socket_path

    def _maintenance_model(self) -> ConfiguredModel:
        if self.model_config is None:
            raise ContextSummaryError(
                "context build requires --model-config or MARGINALIA_MODEL_CONFIG"
            )
        model = load_provider_catalog(self.model_config).resolve(self.maintenance_model_id)
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

    def _catalog(self) -> Any | None:
        if self.model_config is None:
            return None
        try:
            return load_provider_catalog(self.model_config)
        except ProviderConfigurationError:
            return None

    def _session_counter(
        self,
        session: ChatSession,
        policy: Any,
        catalog: Any | None,
    ) -> tuple[TiktokenCounter, CounterIdentity, bool]:
        """Size with the model the next turn will use, as generation would.

        ``ChatSession.model`` is the selection for future turns, so it identifies
        the tokenizer admission will apply. When it cannot be resolved the policy
        tokenizer is used and the result is reported as an estimate rather than a
        readiness guarantee. The identity is returned alongside so a stored
        summary produced by a different counter can be recognised as such.
        """
        if catalog is not None:
            try:
                model = catalog.resolve(session.model)
            except ProviderConfigurationError:
                pass
            else:
                return (
                    TiktokenCounter(model.tokenizer_encoding, model.token_safety_multiplier),
                    CounterIdentity(
                        tokenizer_encoding=model.tokenizer_encoding,
                        token_safety_multiplier=model.token_safety_multiplier,
                    ),
                    False,
                )
        return (
            self._counter(policy),
            CounterIdentity(
                tokenizer_encoding=policy.tokenizer_encoding,
                token_safety_multiplier=policy.token_safety_multiplier,
            ),
            True,
        )

    def _session_window(self, session: ChatSession, catalog: Any | None) -> int | None:
        """The declared context window of the model this session will next use."""
        if catalog is None:
            return None
        try:
            return catalog.resolve(session.model).context_window_tokens
        except ProviderConfigurationError:
            return None

    def _fixed_context(self, project: ProjectRecord) -> list[dict[str, str]]:
        """The same application-owned blocks generation sends with every turn."""
        context_root = self.context_base / project.context_id
        config = CreativeProjectStore(context_root, project.context_id).get()
        return fiction_fixed_context_messages(
            project_config=config,
            governor_dir=context_root / ".governor",
        )

    def plan(
        self,
        *,
        workspace_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Report derived-context readiness using the inputs generation will use.

        Sizing counts the project's real fixed context and the tokenizer of the
        model each session will next use, so this cannot report ready for a
        session the runtime would reject. ``summary_ready`` is tri-state: ``True``
        proven sufficient, ``False`` proven short, ``None`` not provable from the
        available configuration (``estimated`` says which). Only ``True`` counts
        toward the top-level ``ready``.

        Readiness is evaluated against a minimal prompt, since no real prompt
        exists here. A long prompt can still require more coverage; generation
        reports that requirement when it happens and maintenance satisfies it.
        """
        reports = []
        ready = True
        catalog = self._catalog()
        for project in self._projects(workspace_id=workspace_id, project_id=project_id):
            summary_store = self._store(project)
            project_policy = summary_store.policy()
            fixed_error: str | None = None
            try:
                fixed_messages = self._fixed_context(project)
            except CreativeProjectError as exc:
                fixed_messages = []
                fixed_error = f"cannot read project context: {exc}"
            for session in self._sessions(project, session_id=session_id):
                counter, counter_identity, estimated = self._session_counter(
                    session, project_policy, catalog
                )
                # The budget belongs to the model this session will next use, so
                # it is resolved per session rather than once per project.
                window_error: str | None = None
                try:
                    policy = effective_context_policy(
                        project_policy, self._session_window(session, catalog)
                    )
                except ContextTooLarge as exc:
                    policy = project_policy
                    window_error = str(exc)
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
                            "estimated": False,
                            "counter_changed": False,
                            "covered_messages": 0,
                            "required_covered_messages": 0,
                            "error": None,
                        }
                    )
                    continue
                needs_summary = False
                valid_summary = False
                summary_ready = False
                covered = 0
                required_covered = 0
                counter_changed = False
                error = fixed_error or window_error
                try:
                    summary = summary_store.load(session)
                    if summary is not None:
                        valid_summary = True
                        covered = len(summary.source.covered_message_ids)
                        # A summary stays valid across a counter change, but the
                        # coverage it was sized for was decided by a different
                        # counter, so its sufficiency is no longer provable here.
                        counter_changed = not counter_identity.matches(summary.generator.counter)
                except ContextSummaryError as exc:
                    error = str(exc) if error is None else error
                try:
                    # No watermark shortcut: the watermark measures history alone,
                    # while admission measures fixed context plus history plus the
                    # prompt. Asking for the required prefix directly is the same
                    # question generation asks.
                    required_covered = len(
                        choose_summary_prefix(
                            session,
                            fixed_messages,
                            "Continue the story.",
                            policy,
                            counter,
                            additional_reserve_tokens=maintenance_lookahead_tokens(policy),
                        )
                    )
                except ContextSummaryError as exc:
                    error = str(exc) if error is None else error
                needs_summary = required_covered > 0
                # Three outcomes, not two. Reporting a definite shortfall is safe
                # and actionable; claiming readiness from inputs generation will
                # not use is how a wedged session looked healthy. That case is
                # reported as unknown (null) so it can never render as ready.
                summary_ready: bool | None
                if error is not None:
                    summary_ready = False
                elif needs_summary and not (valid_summary and covered >= required_covered):
                    summary_ready = False
                elif estimated or counter_changed:
                    summary_ready = None
                else:
                    summary_ready = True
                if summary_ready is not True:
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
                        "estimated": estimated,
                        "counter_changed": counter_changed,
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
        project: ProjectRecord,
        session: ChatSession,
        messages: list[dict[str, str]],
        configured_model: str,
        maintenance_model: ConfiguredModel,
    ) -> SummaryModelResult:
        context_root = self.context_base / project.context_id
        result = await generate_internal(
            purpose="context-maintenance",
            project_id=project.id,
            context_id=project.context_id,
            context_root=context_root,
            session_id=session.id,
            session_store=SessionStore(context_root / "sessions"),
            generation_store=GenerationStore(context_root / "marginalia" / "generation.sqlite"),
            evidence_store=EncryptedEvidenceStore(
                context_root / "marginalia" / "generation-evidence",
                Path(
                    os.environ.get(
                        "MARGINALIA_EVIDENCE_KEY_FILE",
                        "/run/secrets/marginalia-generation/marginalia-evidence-keys.json",
                    )
                ),
            ),
            messages=messages,
            configured_model=configured_model,
            provider_id=maintenance_model.provider_id,
        )
        return SummaryModelResult(
            content=result.content,
            usage=result.usage,
            receipt_id=result.candidate_id,
            provider_id=result.provider_id,
            model_id=result.model_id,
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
        catalog = load_provider_catalog(self.model_config)
        compatible_models = catalog.compatible_model_ids(maintenance_model)
        for project in self._projects(workspace_id=workspace_id, project_id=project_id):
            store = self._store(project)
            project_policy = store.policy()
            fixed_messages = self._fixed_context(project)
            for session in self._sessions(project, session_id=session_id):
                counter, counter_identity, _estimated = self._session_counter(
                    session, project_policy, catalog
                )
                policy = effective_context_policy(
                    project_policy, self._session_window(session, catalog)
                )
                history_tokens = counter.count_messages(as_messages(session.messages))
                try:
                    existing = store.load(session)
                except ContextSummaryError:
                    existing = None
                # The watermark is not consulted here: a session under it can
                # still need coverage once the project's fixed context and a
                # prompt are counted, and this command exists to repair exactly
                # that. An empty required prefix below still reports not_needed.
                source = choose_summary_prefix(
                    session,
                    fixed_messages,
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
                if existing is not None and len(existing.source.covered_message_ids) >= len(source):
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
                        project,
                        session,
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
                    compatible_configured_models=compatible_models,
                    counter_identity=counter_identity,
                )
                summary = await maintainer.maintain(session, source)
                generation_store = GenerationStore(
                    self.context_base / project.context_id / "marginalia" / "generation.sqlite"
                )
                accept_internal_results(
                    generation_store,
                    summary.generator.receipt_ids,
                    artifact_id=(f"context-summary:{session.id}:{summary.source.prefix_sha256}"),
                )
                reports.append(
                    {
                        "session_ref": _safe_ref(session.id),
                        "status": "built",
                        "covered_messages": len(summary.source.covered_message_ids),
                        "source_sha256": summary.source.prefix_sha256,
                    }
                )
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
