from __future__ import annotations

import json
from pathlib import Path

import pytest

from gov_webui.context_maintenance import ContextMaintainer, SummaryModelResult
from gov_webui.context_summary import (
    ContextPolicy,
    ContextSummaryError,
    ContextSummaryStore,
    ContextTooLarge,
    SummaryFact,
    SummarySections,
    utc_now,
)
from gov_webui.session_store import ChatSession, SessionMessage

from test_context_budget import WordCounter, words


def make_session() -> ChatSession:
    messages = [
        SessionMessage.create("user", words(1_100, "first")),
        SessionMessage.create("assistant", words(1_100, "second")),
        SessionMessage.create("user", words(1_100, "third")),
    ]
    return ChatSession(
        id="maintenance-session",
        context_id="story",
        title="Story",
        created_at=utc_now(),
        updated_at=utc_now(),
        model="codex",
        revision=7,
        message_count=len(messages),
        messages=messages,
    )


def maintenance_policy() -> ContextPolicy:
    return ContextPolicy(
        target_provider_input_tokens=8_000,
        provider_overhead_tokens=4_000,
        output_reserve_tokens=1_000,
        summary_max_tokens=1_000,
        summary_chunk_tokens=2_000,
        updated_at=utc_now(),
    )


def ids_from_prompt(messages: list[dict[str, str]]) -> list[str]:
    source = messages[-1]["content"]
    payload = json.loads(source)
    if payload and "id" in payload[0]:
        return [item["id"] for item in payload]
    result = []
    for chunk in payload:
        result.extend(chunk["source_message_ids"])
    return result


def result_for(messages: list[dict[str, str]], number: int) -> SummaryModelResult:
    ids = ids_from_prompt(messages)
    sections = SummarySections(
        narrative_recap=[
            SummaryFact(text=f"Derived segment {number}", evidence_message_ids=[ids[0]])
        ]
    )
    return SummaryModelResult(
        content=sections.model_dump_json(),
        usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        receipt_id=f"receipt-{number}",
        provider_id="claude-local",
        model_id="sonnet",
    )


@pytest.mark.asyncio
async def test_interrupted_summary_resumes_without_repeating_completed_chunks(
    tmp_path: Path,
) -> None:
    session = make_session()
    before = session.to_dict()
    store = ContextSummaryStore(tmp_path)
    first_calls = 0

    async def fail_second(messages, model):
        nonlocal first_calls
        first_calls += 1
        if first_calls == 2:
            raise RuntimeError("temporary provider failure")
        return result_for(messages, first_calls)

    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=fail_second,
    )
    with pytest.raises(RuntimeError, match="temporary"):
        await maintainer.maintain(session, session.messages)
    assert len(store.load_work(session.id).chunks) == 1
    assert not store.summary_path(session.id).exists()
    assert session.to_dict() == before

    resumed_calls = 0

    async def resume(messages, model):
        nonlocal resumed_calls
        resumed_calls += 1
        return result_for(messages, 100 + resumed_calls)

    resumed = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=resume,
    )
    summary = await resumed.maintain(session, session.messages)

    # Two unfinished source chunks plus two pairwise merges; the completed leaf was reused.
    assert resumed_calls == 4
    assert store.load(session) == summary
    assert len(store.load_work(session.id).chunks) == 3
    assert summary.generator.configured_model == "claude"
    assert summary.generator.provider_id == "claude-local"
    assert session.to_dict() == before


@pytest.mark.asyncio
async def test_interrupted_merge_resumes_without_repeating_completed_merges(
    tmp_path: Path,
) -> None:
    session = make_session()
    session.messages.append(SessionMessage.create("assistant", words(1_100, "fourth")))
    session.message_count = len(session.messages)
    store = ContextSummaryStore(tmp_path)
    calls = 0

    async def fail_second_merge(messages, model):
        nonlocal calls
        calls += 1
        if calls == 6:
            raise RuntimeError("merge route interrupted")
        return result_for(messages, calls)

    first = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=fail_second_merge,
    )
    with pytest.raises(RuntimeError, match="merge route interrupted"):
        await first.maintain(session, session.messages)

    interrupted = store.load_work(session.id)
    assert interrupted is not None
    assert len(interrupted.chunks) == 4
    assert len(interrupted.merges) == 1
    assert not store.summary_path(session.id).exists()

    resumed_calls = 0

    async def resume(messages, model):
        nonlocal resumed_calls
        resumed_calls += 1
        return result_for(messages, 100 + resumed_calls)

    second = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=resume,
    )
    summary = await second.maintain(session, session.messages)

    # The first pairwise merge and all four leaves are reused. Only the second
    # pair and final pair need provider calls.
    assert resumed_calls == 2
    assert len(store.load_work(session.id).merges) == 3
    assert len(summary.generator.receipt_ids) == 7


@pytest.mark.asyncio
async def test_malformed_summary_never_becomes_eligible_context(tmp_path: Path) -> None:
    session = make_session()
    store = ContextSummaryStore(tmp_path)

    async def malformed(messages, model):
        return SummaryModelResult(
            content="not json",
            usage={},
            receipt_id="bad-receipt",
        )

    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=malformed,
    )
    with pytest.raises(ContextSummaryError, match="invalid summary JSON"):
        await maintainer.maintain(session, session.messages)
    assert not store.summary_path(session.id).exists()
    assert store.load(session) is None


@pytest.mark.asyncio
async def test_summary_with_foreign_evidence_is_rejected(tmp_path: Path) -> None:
    session = make_session()
    store = ContextSummaryStore(tmp_path)

    async def foreign(messages, model):
        sections = SummarySections(
            observed_facts=[SummaryFact(text="Invented", evidence_message_ids=["foreign-message"])]
        )
        return SummaryModelResult(
            content=sections.model_dump_json(),
            usage={},
            receipt_id="bad-evidence",
        )

    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=foreign,
    )
    with pytest.raises(ContextSummaryError, match="outside its source chunk"):
        await maintainer.maintain(session, session.messages)
    assert not store.summary_path(session.id).exists()


@pytest.mark.asyncio
async def test_single_message_over_chunk_budget_never_launches_provider(
    tmp_path: Path,
) -> None:
    session = make_session()
    session.messages[0].content = words(2_100, "oversized")
    called = False

    async def should_not_run(messages, model):
        nonlocal called
        called = True
        return result_for(messages, 1)

    maintainer = ContextMaintainer(
        store=ContextSummaryStore(tmp_path),
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=should_not_run,
    )
    with pytest.raises(ContextTooLarge, match="one authored message"):
        await maintainer.maintain(session, session.messages)

    assert called is False


@pytest.mark.asyncio
async def test_oversized_summary_output_is_rejected_before_persistence(
    tmp_path: Path,
) -> None:
    session = make_session()
    session.messages = [SessionMessage.create("user", words(100, "source"))]
    session.message_count = 1
    store = ContextSummaryStore(tmp_path)

    async def oversized(messages, model):
        evidence_id = ids_from_prompt(messages)[0]
        sections = SummarySections(
            narrative_recap=[
                SummaryFact(
                    text=words(20, f"fact-{index}"),
                    evidence_message_ids=[evidence_id],
                )
                for index in range(80)
            ]
        )
        return SummaryModelResult(
            content=sections.model_dump_json(),
            usage={},
            receipt_id="oversized-summary",
        )

    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=oversized,
    )
    with pytest.raises(ContextTooLarge, match="output exceeds"):
        await maintainer.maintain(session, session.messages)

    assert store.load(session) is None


@pytest.mark.asyncio
async def test_expanding_summary_reuses_unchanged_source_chunks(tmp_path: Path) -> None:
    session = make_session()
    store = ContextSummaryStore(tmp_path)
    calls = 0

    async def generate(messages, model):
        nonlocal calls
        calls += 1
        return result_for(messages, calls)

    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=generate,
    )
    first = await maintainer.maintain(session, session.messages[:2])
    first_call_count = calls
    expanded = await maintainer.maintain(session, session.messages)

    assert first_call_count == 3
    assert calls - first_call_count == 2
    assert len(store.load_work(session.id).chunks) == 3
    assert len(expanded.source.covered_message_ids) == 3
    assert first.source.covered_message_ids == [item.id for item in session.messages[:2]]


@pytest.mark.asyncio
async def test_many_chunks_merge_with_bounded_hierarchical_fan_in(tmp_path: Path) -> None:
    session = make_session()
    session.messages = [
        SessionMessage.create(
            "user" if index % 2 == 0 else "assistant",
            words(1_100, f"segment-{index}"),
        )
        for index in range(7)
    ]
    session.message_count = len(session.messages)
    merge_group_sizes: list[int] = []
    calls = 0

    async def generate(messages, model):
        nonlocal calls
        calls += 1
        payload = json.loads(messages[-1]["content"])
        if payload and "source_message_ids" in payload[0]:
            merge_group_sizes.append(len(payload))
            assert "no more than 60 items total" in messages[0]["content"]
        return result_for(messages, calls)

    store = ContextSummaryStore(tmp_path)
    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=generate,
    )

    before = session.to_dict()
    summary = await maintainer.maintain(session, session.messages)

    assert merge_group_sizes == [2, 2, 2, 2, 2, 2]
    assert len(summary.generator.receipt_ids) == 13
    assert len(store.load_work(session.id).merges) == 6
    assert len(store.load_work(session.id).chunks) == 7
    assert session.to_dict() == before


@pytest.mark.asyncio
async def test_dense_merge_rebalances_children_before_parent_provider_call(
    tmp_path: Path,
) -> None:
    session = make_session()
    session.messages = [
        SessionMessage.create("user", words(1_100, "first")),
        SessionMessage.create("assistant", words(1_100, "second")),
    ]
    session.message_count = len(session.messages)
    rebalance_calls = 0

    async def generate(messages, model):
        nonlocal rebalance_calls
        payload = json.loads(messages[-1]["content"])
        if payload and "source_message_ids" in payload[0]:
            if len(payload) == 1:
                assert "too dense for a bounded parent merge" in messages[0]["content"]
                assert "no more than 28 items total" in messages[0]["content"]
                rebalance_calls += 1
            return result_for(messages, 99)
        ids = ids_from_prompt(messages)
        sections = SummarySections(
            observed_facts=[
                SummaryFact(
                    text=f"Dense fact {index}",
                    evidence_message_ids=[ids[0]],
                )
                for index in range(35)
            ]
        )
        return SummaryModelResult(
            content=sections.model_dump_json(),
            usage={},
            receipt_id=f"leaf-{ids[0]}",
        )

    maintainer = ContextMaintainer(
        store=ContextSummaryStore(tmp_path),
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=generate,
    )

    await maintainer.maintain(session, session.messages)
    assert rebalance_calls == 2


@pytest.mark.asyncio
async def test_interrupted_dense_rebalance_resumes_from_child_checkpoint(
    tmp_path: Path,
) -> None:
    session = make_session()
    session.messages = [
        SessionMessage.create("user", words(1_100, "first")),
        SessionMessage.create("assistant", words(1_100, "second")),
    ]
    session.message_count = len(session.messages)
    store = ContextSummaryStore(tmp_path)
    rebalance_calls = 0

    async def interrupted(messages, model):
        nonlocal rebalance_calls
        payload = json.loads(messages[-1]["content"])
        if payload and "source_message_ids" in payload[0]:
            rebalance_calls += 1
            if rebalance_calls == 2:
                raise RuntimeError("dense child interrupted")
            return result_for(messages, 90 + rebalance_calls)
        ids = ids_from_prompt(messages)
        sections = SummarySections(
            observed_facts=[
                SummaryFact(text=f"Dense fact {index}", evidence_message_ids=[ids[0]])
                for index in range(35)
            ]
        )
        return SummaryModelResult(
            content=sections.model_dump_json(),
            usage={},
            receipt_id=f"leaf-{ids[0]}",
        )

    first = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=interrupted,
    )
    with pytest.raises(RuntimeError, match="dense child interrupted"):
        await first.maintain(session, session.messages)

    checkpoint = store.load_work(session.id)
    assert checkpoint is not None
    assert len(checkpoint.chunks) == 2
    assert len(checkpoint.merges) == 1
    assert store.load(session) is None

    resumed_calls = 0

    async def resume(messages, model):
        nonlocal resumed_calls
        resumed_calls += 1
        return result_for(messages, 100 + resumed_calls)

    second = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=resume,
    )
    summary = await second.maintain(session, session.messages)

    assert resumed_calls == 2
    assert len(store.load_work(session.id).merges) == 3
    assert len(summary.source.covered_message_ids) == 2


@pytest.mark.asyncio
async def test_verbose_merge_is_rejected_before_summary_persistence(tmp_path: Path) -> None:
    session = make_session()
    store = ContextSummaryStore(tmp_path)
    calls = 0

    async def generate(messages, model):
        nonlocal calls
        calls += 1
        payload = json.loads(messages[-1]["content"])
        if payload and "source_message_ids" in payload[0]:
            evidence_id = payload[0]["source_message_ids"][0]
            sections = SummarySections(
                observed_facts=[
                    SummaryFact(
                        text=f"Verbose merged fact {index}",
                        evidence_message_ids=[evidence_id],
                    )
                    for index in range(61)
                ]
            )
            return SummaryModelResult(
                content=sections.model_dump_json(),
                usage={},
                receipt_id="verbose-merge",
            )
        return result_for(messages, calls)

    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=generate,
    )

    with pytest.raises(ContextSummaryError, match="structured compaction limits"):
        await maintainer.maintain(session, session.messages)

    assert store.load(session) is None
    assert len(store.load_work(session.id).chunks) == 3
    assert calls == 5


@pytest.mark.asyncio
async def test_verbose_merge_can_recover_with_one_stricter_attempt(tmp_path: Path) -> None:
    session = make_session()
    store = ContextSummaryStore(tmp_path)
    calls = 0
    merge_attempts = 0

    async def generate(messages, model):
        nonlocal calls, merge_attempts
        calls += 1
        payload = json.loads(messages[-1]["content"])
        if payload and "source_message_ids" in payload[0]:
            merge_attempts += 1
            if merge_attempts == 1:
                evidence_id = payload[0]["source_message_ids"][0]
                sections = SummarySections(
                    observed_facts=[
                        SummaryFact(
                            text=f"Verbose merged fact {index}",
                            evidence_message_ids=[evidence_id],
                        )
                        for index in range(61)
                    ]
                )
                return SummaryModelResult(
                    content=sections.model_dump_json(),
                    usage={},
                    receipt_id="invalid-merge",
                )
            if merge_attempts == 2:
                assert "previous merge was unusable" in messages[0]["content"]
                assert "no more than 45 items total" in messages[0]["content"]
        return result_for(messages, calls)

    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=generate,
    )

    summary = await maintainer.maintain(session, session.messages)

    assert merge_attempts == 3
    assert calls == 6
    assert "invalid-merge" not in summary.generator.receipt_ids
    assert len(store.load_work(session.id).merges) == 2
