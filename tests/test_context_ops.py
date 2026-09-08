# SPDX-License-Identifier: Apache-2.0
"""Operator qualification for bounded fiction context."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gov_webui.context_ops import ContextOperations
from gov_webui.context_summary import (
    ContextPolicy,
    ContextSummary,
    ContextSummaryError,
    ContextSummaryStore,
    SummaryFact,
    SummaryGenerator,
    SummarySections,
    source_for,
    utc_now,
)
from gov_webui.library_store import LibraryStore
from gov_webui.ops import migration_preflight
from gov_webui.session_store import SessionMessage, SessionStore
from gov_webui.state_layout import migrate_state_layout


def _seed_session(tmp_path: Path):
    data_root = tmp_path / "data"
    library = LibraryStore(
        data_root / "marginalia" / "library.json",
        default_context_id="erin-writing",
    )
    project = library.default_project()
    sessions = SessionStore(data_root / ".governor" / project.context_id / "sessions")
    created = sessions.create(project.context_id, title="Long project")
    messages = [
        SessionMessage.create("user", " ".join(f"detail-{index}" for index in range(1_200))),
        SessionMessage.create("assistant", "The station clock remains stopped."),
    ]
    assert sessions.append_messages(created.id, messages)
    library.add_conversation(created.id, project.id)
    return data_root, project, sessions.get(created.id)


def _operations(data_root: Path) -> ContextOperations:
    migrate_state_layout(data_root)
    return ContextOperations(
        data_root=data_root,
        default_context_id="erin-writing",
        model_config=None,
        maintenance_model="claude-sonnet-4-20250514",
    )


def test_plan_and_deactivation_do_not_require_a_provider(tmp_path: Path) -> None:
    data_root, project, session = _seed_session(tmp_path)
    store = ContextSummaryStore(data_root / ".governor" / project.context_id)
    store.save_policy(
        ContextPolicy(
            enabled=True,
            target_provider_input_tokens=10_000,
            provider_overhead_tokens=4_000,
            output_reserve_tokens=1_000,
            summary_max_tokens=1_000,
            summary_chunk_tokens=2_000,
            updated_at=utc_now(),
        )
    )
    operations = _operations(data_root)

    plan = operations.plan(project_id=project.id)
    deactivated = operations.activate(enabled=False, project_id=project.id)
    inactive_plan = operations.plan(project_id=project.id)

    assert plan["ready"] is False
    assert plan["sessions"][0]["needs_summary"] is True
    assert plan["sessions"][0]["session_ref"] != session.id
    assert "detail-1199" not in str(plan)
    assert deactivated == {
        "ready": True,
        "projects": [{"project_id": project.id, "enabled": False}],
    }
    assert store.policy().enabled is False
    assert inactive_plan["ready"] is True
    assert inactive_plan["sessions"][0]["summary_ready"] is True


def test_plan_rejects_valid_summary_that_covers_too_little_history(tmp_path: Path) -> None:
    data_root, project, session = _seed_session(tmp_path)
    store = ContextSummaryStore(data_root / ".governor" / project.context_id)
    store.save_policy(
        ContextPolicy(
            enabled=True,
            target_provider_input_tokens=10_000,
            provider_overhead_tokens=4_000,
            output_reserve_tokens=1_000,
            summary_max_tokens=1_000,
            summary_chunk_tokens=2_000,
            updated_at=utc_now(),
        )
    )
    source_message = session.messages[0]
    store.save(
        ContextSummary(
            source=source_for(session, [source_message]),
            generator=SummaryGenerator(
                configured_model="claude-sonnet-4-20250514",
                provider_id="claude-code-local",
                model_id="sonnet",
            ),
            created_at=utc_now(),
            sections=SummarySections(
                observed_facts=[
                    SummaryFact(
                        text="The project includes a station.",
                        evidence_message_ids=[source_message.id],
                    )
                ]
            ),
        )
    )

    plan = _operations(data_root).plan(project_id=project.id)

    report = plan["sessions"][0]
    assert report["summary_valid"] is True
    assert report["summary_ready"] is False
    assert report["covered_messages"] == 1
    assert report["required_covered_messages"] == 2
    assert plan["ready"] is False


def test_plan_counts_the_project_context_generation_actually_sends(tmp_path: Path) -> None:
    """Readiness must be sized against the real fixed context, not an empty list.

    Planning with no project context under-states the coverage generation needs,
    which is how a session that the runtime rejects could be reported ready.
    """
    from gov_webui.context_budget import (
        TiktokenCounter,
        choose_summary_prefix,
        maintenance_lookahead_tokens,
    )
    from gov_webui.creative_project import CreativeProjectStore

    data_root = tmp_path / "data"
    library = LibraryStore(
        data_root / "marginalia" / "library.json",
        default_context_id="erin-writing",
    )
    project = library.default_project()
    sessions = SessionStore(data_root / ".governor" / project.context_id / "sessions")
    created = sessions.create(project.context_id, title="Bible-heavy project")
    messages = []
    for _ in range(10):
        messages.append(SessionMessage.create("user", " ".join(["story"] * 200)))
        messages.append(SessionMessage.create("assistant", " ".join(["reply"] * 200)))
    assert sessions.append_messages(created.id, messages)
    library.add_conversation(created.id, project.id)
    session = sessions.get(created.id)

    context_root = data_root / ".governor" / project.context_id
    store = ContextSummaryStore(context_root)
    policy = store.save_policy(
        ContextPolicy(
            enabled=True,
            target_provider_input_tokens=12_000,
            provider_overhead_tokens=4_000,
            output_reserve_tokens=1_000,
            summary_max_tokens=1_000,
            summary_chunk_tokens=2_000,
            updated_at=utc_now(),
        )
    )

    # A real story bible, stored where generation reads it from.
    CreativeProjectStore(context_root, project.context_id).update(
        project_brief=" ".join(["bible"] * 300),
        collaborator_stance="Be exacting.",
        voice_style_guidance="Tactile.",
    )

    counter = TiktokenCounter(policy.tokenizer_encoding, policy.token_safety_multiplier)
    lookahead = maintenance_lookahead_tokens(policy)
    operations = _operations(data_root)
    fixed = operations._fixed_context(project)
    assert fixed, "the project context must be visible to the planner"

    without_fixed = len(
        choose_summary_prefix(
            session, [], "Continue the story.", policy, counter, additional_reserve_tokens=lookahead
        )
    )
    with_fixed = len(
        choose_summary_prefix(
            session,
            fixed,
            "Continue the story.",
            policy,
            counter,
            additional_reserve_tokens=lookahead,
        )
    )
    assert with_fixed > without_fixed, "the story bible must raise the requirement"

    plan = operations.plan(project_id=project.id)
    report = plan["sessions"][0]
    assert report["required_covered_messages"] == with_fixed
    assert report["summary_ready"] is not True
    assert plan["ready"] is False


def test_plan_reports_unknown_rather_than_ready_when_it_cannot_size_like_runtime(
    tmp_path: Path,
) -> None:
    """An unprovable verdict must never render as ready."""
    data_root, project, session = _seed_session(tmp_path)
    context_root = data_root / ".governor" / project.context_id
    store = ContextSummaryStore(context_root)
    store.save_policy(
        ContextPolicy(
            enabled=True,
            target_provider_input_tokens=64_000,
            provider_overhead_tokens=4_000,
            output_reserve_tokens=1_000,
            summary_max_tokens=1_000,
            summary_chunk_tokens=2_000,
            updated_at=utc_now(),
        )
    )

    # Budget is generous enough that no summary is needed, but with no model
    # catalog the planner cannot size with the tokenizer the next turn will use.
    plan = _operations(data_root).plan(project_id=project.id)
    report = plan["sessions"][0]
    assert report["needs_summary"] is False
    assert report["estimated"] is True
    assert report["summary_ready"] is None
    assert plan["ready"] is False


def test_build_without_provider_configuration_fails_before_writing(tmp_path: Path) -> None:
    data_root, project, session = _seed_session(tmp_path)
    store = ContextSummaryStore(data_root / ".governor" / project.context_id)
    before = session.to_dict()

    with pytest.raises(ContextSummaryError, match="context build requires"):
        asyncio.run(_operations(data_root).build(project_id=project.id))

    current = SessionStore(data_root / ".governor" / project.context_id / "sessions").get(
        session.id
    )
    assert current is not None
    assert current.to_dict() == before
    assert store.load(current) is None


def test_preflight_rejects_summary_whose_source_story_was_mutated(tmp_path: Path) -> None:
    data_root, project, session = _seed_session(tmp_path)
    store = ContextSummaryStore(data_root / ".governor" / project.context_id)
    source_message = session.messages[0]
    store.save(
        ContextSummary(
            source=source_for(session, [source_message]),
            generator=SummaryGenerator(
                configured_model="claude-sonnet-4-20250514",
                provider_id="claude-code-local",
                model_id="sonnet",
            ),
            created_at=utc_now(),
            sections=SummarySections(
                observed_facts=[
                    SummaryFact(
                        text="The project includes a station.",
                        evidence_message_ids=[source_message.id],
                    )
                ]
            ),
        )
    )
    healthy = migration_preflight(
        data_root=data_root,
        default_context_id="erin-writing",
    )

    session_path = next((data_root / ".governor" / project.context_id / "sessions").glob("*.json"))
    raw = json.loads(session_path.read_text(encoding="utf-8"))
    raw["messages"][0]["content"] = "Mutated source that no longer matches."
    session_path.write_text(json.dumps(raw), encoding="utf-8")
    damaged = migration_preflight(
        data_root=data_root,
        default_context_id="erin-writing",
    )

    assert healthy["ready"] is True
    assert damaged["ready"] is False
    assert any("context summary" in error for error in damaged["errors"])


def test_plan_reports_a_counter_change_instead_of_asserting_readiness(tmp_path: Path) -> None:
    """A summary sized by a different counter is valid but no longer provably enough."""
    from gov_webui.context_summary import CounterIdentity

    data_root, project, session = _seed_session(tmp_path)
    context_root = data_root / ".governor" / project.context_id
    store = ContextSummaryStore(context_root)
    store.save_policy(
        ContextPolicy(
            enabled=True,
            target_provider_input_tokens=64_000,
            provider_overhead_tokens=4_000,
            output_reserve_tokens=1_000,
            summary_max_tokens=1_000,
            summary_chunk_tokens=2_000,
            updated_at=utc_now(),
        )
    )
    source_message = session.messages[0]

    def save_summary(counter: CounterIdentity | None) -> None:
        store.save(
            ContextSummary(
                source=source_for(session, [source_message]),
                generator=SummaryGenerator(
                    configured_model="claude-sonnet-4-20250514",
                    counter=counter,
                ),
                created_at=utc_now(),
                sections=SummarySections(
                    observed_facts=[
                        SummaryFact(
                            text="The project includes a station.",
                            evidence_message_ids=[source_message.id],
                        )
                    ]
                ),
            )
        )

    operations = _operations(data_root)
    policy = store.policy()
    _, identity, _ = operations._session_counter(session, policy, operations._catalog())

    # Produced by the counter now in use: no change to report.
    save_summary(identity)
    report = operations.plan(project_id=project.id)["sessions"][0]
    assert report["counter_changed"] is False

    # Produced by a different counter: valid, but sufficiency is not provable.
    save_summary(
        CounterIdentity(
            tokenizer_encoding=identity.tokenizer_encoding,
            token_safety_multiplier=identity.token_safety_multiplier + 0.5,
        )
    )
    report = operations.plan(project_id=project.id)["sessions"][0]
    assert report["summary_valid"] is True
    assert report["counter_changed"] is True
    assert report["summary_ready"] is not True
    assert operations.plan(project_id=project.id)["ready"] is False

    # A legacy summary with no recorded identity is unknown, not a mismatch.
    save_summary(None)
    report = operations.plan(project_id=project.id)["sessions"][0]
    assert report["counter_changed"] is False
