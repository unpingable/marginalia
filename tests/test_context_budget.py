from __future__ import annotations

from pathlib import Path

import pytest

from gov_webui.context_budget import (
    build_generation_context,
    choose_summary_prefix,
    maintenance_lookahead_tokens,
)
from gov_webui.context_summary import (
    ContextMaintenanceRequired,
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
from gov_webui.session_store import ChatSession, SessionMessage


class WordCounter:
    def count_text(self, text: str) -> int:
        return len(text.split())

    def count_messages(self, messages) -> int:
        return sum(self.count_text(item["content"]) + 1 for item in messages)


def words(count: int, value: str = "story") -> str:
    return " ".join([value] * count)


def session_with_pairs(pair_count: int, tokens_each: int = 900) -> ChatSession:
    messages = []
    for index in range(pair_count):
        messages.extend(
            [
                SessionMessage.create("user", words(tokens_each, f"u{index}")),
                SessionMessage.create("assistant", words(tokens_each, f"a{index}")),
            ]
        )
    return ChatSession(
        id="session",
        context_id="context",
        title="Story",
        created_at=utc_now(),
        updated_at=utc_now(),
        model="codex-default",
        revision=pair_count,
        message_count=len(messages),
        messages=messages,
    )


def policy(enabled: bool = True) -> ContextPolicy:
    return ContextPolicy(
        enabled=enabled,
        target_provider_input_tokens=8_000,
        provider_overhead_tokens=4_000,
        output_reserve_tokens=1_000,
        summary_max_tokens=1_000,
        summary_chunk_tokens=2_000,
        updated_at=utc_now(),
    )


def summary_for(session: ChatSession, prefix: list[SessionMessage]) -> ContextSummary:
    return ContextSummary(
        source=source_for(session, prefix),
        generator=SummaryGenerator(
            configured_model="claude",
            provider_id="claude-local",
            model_id="sonnet",
            receipt_ids=["receipt"],
        ),
        created_at=utc_now(),
        usage={"prompt_tokens": 10},
        sections=SummarySections(
            narrative_recap=[
                SummaryFact(
                    text="The opening events remain relevant.",
                    evidence_message_ids=[prefix[0].id],
                )
            ]
        ),
    )


def test_short_history_uses_complete_durable_context_without_compaction() -> None:
    session = session_with_pairs(1, 100)
    built = build_generation_context(
        session=session,
        pending_user="Continue.",
        fixed_messages=[{"role": "system", "content": "Project direction"}],
        policy=policy(),
        counter=WordCounter(),
        summary=None,
    )

    assert built.messages[1:-1] == [
        {"role": item.role, "content": item.content} for item in session.messages
    ]
    assert built.metrics.compacted is False
    assert built.metrics.summarized_message_count == 0
    assert built.metrics.predicted_provider_tokens <= 8_000


def test_long_history_requires_typed_maintenance_not_truncation() -> None:
    session = session_with_pairs(3)
    with pytest.raises(ContextMaintenanceRequired):
        build_generation_context(
            session=session,
            pending_user="Continue.",
            fixed_messages=[{"role": "system", "content": words(100)}],
            policy=policy(),
            counter=WordCounter(),
            summary=None,
        )


def test_prefix_selection_and_summary_create_no_gap_or_overlap() -> None:
    session = session_with_pairs(3)
    prefix = choose_summary_prefix(
        session,
        [{"role": "system", "content": words(100)}],
        "Continue.",
        policy(),
        WordCounter(),
    )
    assert len(prefix) % 2 == 0
    summary = summary_for(session, prefix)

    built = build_generation_context(
        session=session,
        pending_user="Continue.",
        fixed_messages=[{"role": "system", "content": words(100)}],
        policy=policy(),
        counter=WordCounter(),
        summary=summary,
    )

    recent = session.messages[len(prefix) :]
    assert built.metrics.compacted is True
    assert built.metrics.summarized_message_count == len(prefix)
    assert built.metrics.recent_message_count == len(recent)
    assert [item["content"] for item in built.messages[-len(recent) - 1 : -1]] == [
        item.content for item in recent
    ]
    assert not any(item.content in str(built.messages) for item in prefix)


def test_summary_store_rejects_changed_source_but_accepts_later_append(tmp_path: Path) -> None:
    session = session_with_pairs(2, 100)
    store = ContextSummaryStore(tmp_path)
    summary = summary_for(session, session.messages[:2])
    store.save(summary)

    session.messages.extend(
        [
            SessionMessage.create("user", "A later prompt"),
            SessionMessage.create("assistant", "A later passage"),
        ]
    )
    session.revision += 1
    assert store.load(session) == summary

    session.messages[0].content = "Changed source prose"
    with pytest.raises(ContextSummaryError, match="content no longer matches"):
        store.load(session)


def test_policy_activation_is_reversible_and_separate_from_summary(tmp_path: Path) -> None:
    store = ContextSummaryStore(tmp_path)
    assert store.policy().enabled is False
    assert store.set_enabled(True).enabled is True
    assert store.set_enabled(False).enabled is False


def test_observed_51_covered_58_required_shape_fails_closed_until_ready() -> None:
    messages = [
        SessionMessage.create("assistant", words(90, f"passage-{index}")) for index in range(90)
    ]
    session = ChatSession(
        id="incident-session",
        context_id="story",
        title="Long story",
        created_at=utc_now(),
        updated_at=utc_now(),
        model="codex-default",
        revision=18,
        message_count=len(messages),
        messages=messages,
    )
    incident_policy = policy()
    counter = WordCounter()
    required = choose_summary_prefix(
        session,
        [],
        "Continue.",
        incident_policy,
        counter,
    )
    assert len(required) == 58

    stale = ContextSummary(
        source=source_for(session, session.messages[:51]),
        generator=SummaryGenerator(configured_model="claude"),
        created_at=utc_now(),
        sections=SummarySections(
            narrative_recap=[
                SummaryFact(
                    text=words(500, "s"),
                    evidence_message_ids=[session.messages[0].id],
                )
            ]
        ),
    )
    with pytest.raises(ContextMaintenanceRequired, match="does not cover enough"):
        build_generation_context(
            session=session,
            pending_user="Continue.",
            fixed_messages=[],
            policy=incident_policy,
            counter=counter,
            summary=stale,
        )

    ready = stale.model_copy(update={"source": source_for(session, session.messages[:58])})
    built = build_generation_context(
        session=session,
        pending_user="Continue.",
        fixed_messages=[],
        policy=incident_policy,
        counter=counter,
        summary=ready,
    )
    assert built.metrics.summarized_message_count == 58


def test_proactive_lookahead_covers_more_than_the_immediate_minimum() -> None:
    session = session_with_pairs(3)
    incident_policy = policy()
    immediate = choose_summary_prefix(session, [], "Continue.", incident_policy, WordCounter())
    proactive = choose_summary_prefix(
        session,
        [],
        "Continue.",
        incident_policy,
        WordCounter(),
        additional_reserve_tokens=maintenance_lookahead_tokens(incident_policy),
    )

    assert len(proactive) > len(immediate)


def test_interactive_context_requires_the_same_lookahead_as_prebuild() -> None:
    session = session_with_pairs(3)
    incident_policy = policy()
    counter = WordCounter()
    immediate = choose_summary_prefix(session, [], "Continue.", incident_policy, counter)
    proactive = choose_summary_prefix(
        session,
        [],
        "Continue.",
        incident_policy,
        counter,
        additional_reserve_tokens=maintenance_lookahead_tokens(incident_policy),
    )
    assert len(immediate) < len(proactive)

    with pytest.raises(ContextMaintenanceRequired, match="does not cover enough"):
        build_generation_context(
            session=session,
            pending_user="Continue.",
            fixed_messages=[],
            policy=incident_policy,
            counter=counter,
            summary=summary_for(session, immediate),
            additional_reserve_tokens=maintenance_lookahead_tokens(incident_policy),
        )

    built = build_generation_context(
        session=session,
        pending_user="Continue.",
        fixed_messages=[],
        policy=incident_policy,
        counter=counter,
        summary=summary_for(session, proactive),
        additional_reserve_tokens=maintenance_lookahead_tokens(incident_policy),
    )
    assert built.metrics.summarized_message_count == len(proactive)
    assert built.metrics.application_tokens <= (
        incident_policy.application_tokens - maintenance_lookahead_tokens(incident_policy)
    )
