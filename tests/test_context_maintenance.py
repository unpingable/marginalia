from __future__ import annotations

import asyncio

import json
from pathlib import Path

import pytest

from gov_webui.context_maintenance import ContextMaintainer, SummaryModelResult
from gov_webui.context_summary import (
    ContextPolicy,
    ContextSummary,
    ContextSummaryError,
    ContextSummaryStore,
    ContextTooLarge,
    SummaryFact,
    SummaryGenerator,
    SummarySections,
    source_for,
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
        # One chunk in flight keeps the injected failure point deterministic.
        # Resume-without-repeat is the property here, not concurrency.
        chunk_concurrency=1,
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
async def test_checkpoint_survives_configured_alias_change_for_same_upstream(
    tmp_path: Path,
) -> None:
    session = make_session()
    store = ContextSummaryStore(tmp_path)
    first_calls = 0

    async def interrupt(messages, model):
        nonlocal first_calls
        first_calls += 1
        if first_calls == 2:
            raise RuntimeError("pause after first checkpoint")
        return result_for(messages, first_calls)

    first = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude-writing-alias",
        provider_id="claude-local",
        model_id="sonnet",
        generate=interrupt,
        chunk_concurrency=1,
    )
    with pytest.raises(RuntimeError, match="pause after first"):
        await first.maintain(session, session.messages)
    assert len(store.load_work(session.id).chunks) == 1

    resumed_calls = 0

    async def resume(messages, model):
        nonlocal resumed_calls
        resumed_calls += 1
        return result_for(messages, 100 + resumed_calls)

    renamed = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude-context-summary",
        provider_id="claude-local",
        model_id="sonnet",
        generate=resume,
        compatible_configured_models=frozenset({"claude-writing-alias", "claude-context-summary"}),
    )
    summary = await renamed.maintain(session, session.messages)

    assert resumed_calls == 4
    assert summary.generator.configured_model == "claude-context-summary"
    assert store.load_work(session.id).generator_model == "claude-context-summary"


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
                ids = ids_from_prompt(messages)
                sections = SummarySections(
                    observed_facts=[SummaryFact(text="x" * 298, evidence_message_ids=ids[:4])]
                )
                return SummaryModelResult(
                    content=sections.model_dump_json(),
                    usage={},
                    receipt_id=f"dense-child-{rebalance_calls}",
                )
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
        # One chunk in flight keeps the injected failure point deterministic.
        # Resume-without-repeat is the property here, not concurrency.
        chunk_concurrency=1,
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


def _long_session(pairs: int) -> ChatSession:
    messages = []
    for index in range(pairs):
        messages.append(SessionMessage.create("user", words(1_100, f"u{index}")))
        messages.append(SessionMessage.create("assistant", words(1_100, f"a{index}")))
    return ChatSession(
        id="maintenance-session",
        context_id="story",
        title="Story",
        created_at=utc_now(),
        updated_at=utc_now(),
        model="codex",
        revision=9,
        message_count=len(messages),
        messages=messages,
    )


@pytest.mark.asyncio
async def test_shorter_requirement_prunes_cached_work_instead_of_discarding_it(
    tmp_path: Path,
) -> None:
    """A smaller requirement must not throw away completed provider work.

    Chunks are content-addressed and packed left to right, so the leading chunks
    of a shorter prefix are the same ones already paid for. Rebuilding from empty
    re-ran every provider call for no benefit.
    """
    session = _long_session(6)
    store = ContextSummaryStore(tmp_path)

    def maintainer_for(counter_box):
        async def generate(messages, model):
            counter_box[0] += 1
            return result_for(messages, counter_box[0])

        return ContextMaintainer(
            store=store,
            policy=maintenance_policy(),
            counter=WordCounter(),
            configured_model="claude",
            provider_id="claude-local",
            model_id="sonnet",
            generate=generate,
        )

    wide = [0]
    await maintainer_for(wide).maintain(session, session.messages[:12])
    assert wide[0] > 0
    cached = store.load_work(session.id)
    assert len(cached.source.covered_message_ids) == 12

    # Now a turn that needs less coverage than the cached work already holds.
    narrow = [0]
    summary = await maintainer_for(narrow).maintain(session, session.messages[:8])

    assert len(summary.source.covered_message_ids) == 8
    # Leading chunks were reused, so far fewer provider calls than a cold rebuild.
    cold = [0]
    fresh_store = ContextSummaryStore(tmp_path / "cold")
    cold_maintainer = ContextMaintainer(
        store=fresh_store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=lambda messages, model: _count_and_result(cold, messages),
    )
    await cold_maintainer.maintain(session, session.messages[:8])
    assert narrow[0] < cold[0], f"reuse saved nothing: narrow={narrow[0]} cold={cold[0]}"


async def _count_and_result(box, messages):
    box[0] += 1
    return result_for(messages, box[0])


@pytest.mark.asyncio
async def test_reuse_still_rejects_cached_work_whose_content_changed(
    tmp_path: Path,
) -> None:
    """Prefix reuse must remain content-addressed, not id-addressed."""
    session = _long_session(6)
    store = ContextSummaryStore(tmp_path)
    first = [0]

    async def generate(messages, model):
        first[0] += 1
        return result_for(messages, first[0])

    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=generate,
    )
    await maintainer.maintain(session, session.messages[:12])
    paid = first[0]

    # Same message ids, different authored content.
    edited = session.messages[:8]
    edited[0] = SessionMessage(
        id=edited[0].id,
        role=edited[0].role,
        content=words(1_100, "rewritten"),
        timestamp=edited[0].timestamp,
    )
    tampered = ChatSession(
        id=session.id,
        context_id=session.context_id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        model=session.model,
        revision=session.revision + 1,
        message_count=len(edited),
        messages=edited,
    )
    second = [0]

    async def generate2(messages, model):
        second[0] += 1
        return result_for(messages, second[0])

    maintainer2 = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=generate2,
    )
    summary = await maintainer2.maintain(tampered, edited)

    assert second[0] > 0, "changed content must be re-summarised, never reused"
    assert len(summary.source.covered_message_ids) == 8
    assert paid > 0


@pytest.mark.asyncio
async def test_summary_and_work_record_the_counter_that_sized_them(tmp_path: Path) -> None:
    """Counter identity is provenance: without it a counter change is invisible."""
    from gov_webui.context_summary import CounterIdentity

    session = make_session()
    store = ContextSummaryStore(tmp_path)
    calls = [0]

    async def generate(messages, model):
        calls[0] += 1
        return result_for(messages, calls[0])

    identity = CounterIdentity(tokenizer_encoding="o200k_base", token_safety_multiplier=1.0)
    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=generate,
        counter_identity=identity,
    )
    summary = await maintainer.maintain(session, session.messages)

    assert summary.generator.counter == identity
    assert store.load_work(session.id).counter == identity
    # It survives the round trip through disk.
    assert store.load(session).generator.counter == identity


@pytest.mark.asyncio
async def test_checkpoint_is_not_reused_across_a_counter_change(tmp_path: Path) -> None:
    """Chunk boundaries and size bounds belong to the counter that chose them."""
    from gov_webui.context_summary import CounterIdentity

    session = _long_session(6)
    store = ContextSummaryStore(tmp_path)

    def maintainer_for(box, identity):
        async def generate(messages, model):
            box[0] += 1
            return result_for(messages, box[0])

        return ContextMaintainer(
            store=store,
            policy=maintenance_policy(),
            counter=WordCounter(),
            configured_model="claude",
            provider_id="claude-local",
            model_id="sonnet",
            generate=generate,
            counter_identity=identity,
        )

    first = [0]
    original = CounterIdentity(tokenizer_encoding="o200k_base", token_safety_multiplier=1.0)
    await maintainer_for(first, original).maintain(session, session.messages[:8])
    assert first[0] > 0

    # Same counter: the checkpoint is reused, so no provider work repeats.
    same = [0]
    await maintainer_for(same, original).maintain(session, session.messages[:8])
    assert same[0] == 0

    # Different safety multiplier: the checkpoint no longer describes this budget.
    changed = [0]
    other = CounterIdentity(tokenizer_encoding="o200k_base", token_safety_multiplier=1.5)
    await maintainer_for(changed, other).maintain(session, session.messages[:8])
    assert changed[0] > 0, "a counter change must not silently reuse the old checkpoint"
    assert store.load_work(session.id).counter == other


@pytest.mark.asyncio
async def test_legacy_checkpoint_without_counter_identity_still_resumes(tmp_path: Path) -> None:
    """Records written before identity was tracked are unknown, not incompatible."""
    from gov_webui.context_summary import CounterIdentity

    session = _long_session(6)
    store = ContextSummaryStore(tmp_path)
    first = [0]

    async def generate(messages, model):
        first[0] += 1
        return result_for(messages, first[0])

    legacy = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=generate,
    )
    await legacy.maintain(session, session.messages[:8])
    assert store.load_work(session.id).counter is None
    assert first[0] > 0

    second = [0]

    async def generate2(messages, model):
        second[0] += 1
        return result_for(messages, second[0])

    upgraded = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=generate2,
        counter_identity=CounterIdentity(
            tokenizer_encoding="o200k_base", token_safety_multiplier=1.0
        ),
    )
    await upgraded.maintain(session, session.messages[:8])
    assert second[0] == 0, "a legacy checkpoint must not be discarded for lacking identity"
    # The upgraded run stamps identity onto the record it inherited.
    assert store.load_work(session.id).counter is not None


@pytest.mark.asyncio
async def test_independent_chunks_run_concurrently(tmp_path: Path) -> None:
    """Leaf summaries are independent, so latency need not be chunk x round-trip."""
    session = make_session()
    store = ContextSummaryStore(tmp_path)
    in_flight = 0
    peak = 0
    calls = 0

    async def slow(messages, model):
        nonlocal in_flight, peak, calls
        calls += 1
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)  # yield so siblings can overlap
        in_flight -= 1
        return result_for(messages, calls)

    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=slow,
        chunk_concurrency=3,
    )
    await maintainer.maintain(session, session.messages)

    assert peak > 1, "leaf chunks must overlap rather than run strictly in series"
    assert peak <= 3, "concurrency must stay within the configured bound"


@pytest.mark.asyncio
async def test_concurrency_bound_is_respected(tmp_path: Path) -> None:
    """The bound exists so maintenance cannot crowd out the writer's own turn."""
    session = make_session()
    store = ContextSummaryStore(tmp_path)
    in_flight = 0
    peak = 0
    calls = 0

    async def slow(messages, model):
        nonlocal in_flight, peak, calls
        calls += 1
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return result_for(messages, calls)

    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=slow,
        chunk_concurrency=1,
    )
    await maintainer.maintain(session, session.messages)
    assert peak == 1


@pytest.mark.asyncio
async def test_a_failing_batch_does_not_pay_for_later_batches(tmp_path: Path) -> None:
    """A provider that refuses one chunk must not be handed every remaining one."""
    session = make_session()
    store = ContextSummaryStore(tmp_path)
    calls = 0

    async def fail_first(messages, model):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider is down")

    maintainer = ContextMaintainer(
        store=store,
        policy=maintenance_policy(),
        counter=WordCounter(),
        configured_model="claude",
        provider_id="claude-local",
        model_id="sonnet",
        generate=fail_first,
        chunk_concurrency=1,
    )
    with pytest.raises(RuntimeError, match="provider is down"):
        await maintainer.maintain(session, session.messages)

    # One batch of one attempted, then stop — not one call per remaining chunk.
    assert calls == 1


def test_a_summary_with_no_facts_is_rejected() -> None:
    """`{}` is structurally valid and would claim coverage while saying nothing.

    Every section defaults to an empty list, so an empty object parses. Promoting
    it would satisfy the coverage check with derived context that contains no
    facts at all — the session's whole history summarised into silence.
    """
    from gov_webui.context_summary import parse_summary_sections

    with pytest.raises(ContextSummaryError, match="no facts"):
        parse_summary_sections("{}")

    with pytest.raises(ContextSummaryError, match="no facts"):
        parse_summary_sections('{"narrative_recap": [], "observed_facts": []}')


def test_a_summary_with_one_fact_is_accepted() -> None:
    from gov_webui.context_summary import parse_summary_sections

    sections = parse_summary_sections(
        '{"narrative_recap": [{"text": "Maren reached the dock.",'
        ' "evidence_message_ids": ["m1"], "confidence": "established"}]}'
    )
    assert len(sections.narrative_recap) == 1


def _write_work(store, session, covered):
    from gov_webui.context_summary import SummaryWork, source_for, utc_now

    store.save_work(
        SummaryWork(
            source=source_for(session, covered),
            generator_model="claude",
            chunks=[],
            updated_at=utc_now(),
        )
    )


def test_interrupted_sessions_finds_work_without_a_summary(tmp_path: Path) -> None:
    """A container replacement mid-run leaves finished chunks with nothing to
    carry them forward; reconciliation has to be able to find them."""
    session = make_session()
    store = ContextSummaryStore(tmp_path)
    _write_work(store, session, session.messages[:2])

    assert store.interrupted_sessions() == [session.id]


def test_a_session_whose_summary_caught_up_is_not_interrupted(tmp_path: Path) -> None:
    session = make_session()
    store = ContextSummaryStore(tmp_path)
    _write_work(store, session, session.messages[:2])
    store.save(
        ContextSummary(
            source=source_for(session, session.messages[:2]),
            generator=SummaryGenerator(configured_model="claude"),
            created_at=utc_now(),
            sections=SummarySections(
                narrative_recap=[
                    SummaryFact(text="Derived.", evidence_message_ids=[session.messages[0].id])
                ]
            ),
        )
    )

    assert store.interrupted_sessions() == []


def test_a_summary_behind_its_work_is_interrupted(tmp_path: Path) -> None:
    session = make_session()
    store = ContextSummaryStore(tmp_path)
    _write_work(store, session, session.messages[:4])
    store.save(
        ContextSummary(
            source=source_for(session, session.messages[:2]),
            generator=SummaryGenerator(configured_model="claude"),
            created_at=utc_now(),
            sections=SummarySections(
                narrative_recap=[
                    SummaryFact(text="Derived.", evidence_message_ids=[session.messages[0].id])
                ]
            ),
        )
    )

    assert store.interrupted_sessions() == [session.id]


def test_session_ids_survive_the_filename_round_trip(tmp_path: Path) -> None:
    """`Path.stem` yields "<id>.work" and would make every id wrong."""
    session = make_session()
    store = ContextSummaryStore(tmp_path)
    _write_work(store, session, session.messages[:2])

    found = store.interrupted_sessions()
    assert found == [session.id]
    assert not any(name.endswith(".work") for name in found)


def test_unreadable_checkpoints_are_skipped_not_fatal(tmp_path: Path) -> None:
    """Reconciliation runs at startup and must not stop the app from serving."""
    store = ContextSummaryStore(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "broken.work.json").write_text("{not json", encoding="utf-8")

    assert store.interrupted_sessions() == []


def test_no_context_directory_is_not_an_error(tmp_path: Path) -> None:
    assert ContextSummaryStore(tmp_path / "absent").interrupted_sessions() == []
