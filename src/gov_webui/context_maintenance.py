# SPDX-License-Identifier: Apache-2.0
"""Bounded, resumable maintenance of derived fiction summaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from gov_webui.context_budget import TokenCounter, as_messages
from gov_webui.context_summary import (
    ContextPolicy,
    ContextSummary,
    ContextSummaryError,
    ContextTooLarge,
    ContextSummaryStore,
    SummaryGenerator,
    SummarySections,
    SummaryWork,
    SummaryWorkChunk,
    message_prefix_hash,
    parse_summary_sections,
    source_for,
    summary_prompt,
    utc_now,
)
from gov_webui.session_store import ChatSession, SessionMessage


@dataclass(frozen=True)
class SummaryModelResult:
    """Validated provider output that is eligible only for summary parsing."""

    content: str
    usage: dict[str, int]
    receipt_id: str
    provider_id: str | None = None
    model_id: str | None = None


MERGED_SUMMARY_MAX_FACTS = 60
MERGED_SUMMARY_MAX_TEXT_CHARS = 300
MERGED_SUMMARY_MAX_EVIDENCE_IDS = 4
DENSE_CHILD_TARGET_FACTS = 28
DENSE_CHILD_MAX_FACTS = 32
DENSE_CHILD_MAX_TEXT_CHARS = 240
DENSE_CHILD_MAX_EVIDENCE_IDS = 3


def _validate_merged_sections(sections: SummarySections) -> None:
    _validate_sections_with_limits(
        sections,
        max_facts=MERGED_SUMMARY_MAX_FACTS,
        max_text_chars=MERGED_SUMMARY_MAX_TEXT_CHARS,
        max_evidence_ids=MERGED_SUMMARY_MAX_EVIDENCE_IDS,
    )


def _validate_sections_with_limits(
    sections: SummarySections,
    *,
    max_facts: int,
    max_text_chars: int,
    max_evidence_ids: int,
) -> None:
    items = [item for name in sections.__class__.model_fields for item in getattr(sections, name)]
    longest_text = max((len(item.text) for item in items), default=0)
    most_evidence = max((len(item.evidence_message_ids) for item in items), default=0)
    if len(items) > max_facts or longest_text > max_text_chars or most_evidence > max_evidence_ids:
        raise ContextSummaryError(
            "merged context summary exceeds structured compaction limits "
            f"(items={len(items)}, longest_text={longest_text}, "
            f"most_evidence={most_evidence})"
        )


SummaryGeneratorCall = Callable[[list[dict[str, str]], str], Awaitable[SummaryModelResult]]


def _chunks(
    messages: list[SessionMessage],
    *,
    max_tokens: int,
    counter: TokenCounter,
) -> list[list[SessionMessage]]:
    chunks: list[list[SessionMessage]] = []
    current: list[SessionMessage] = []
    for message in messages:
        if counter.count_messages(as_messages([message])) > max_tokens:
            raise ContextTooLarge(
                "one authored message exceeds the context-maintenance chunk budget"
            )
        candidate = [*current, message]
        if current and counter.count_messages(as_messages(candidate)) > max_tokens:
            chunks.append(current)
            current = [message]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _merge_prompt(
    chunks: list[SummaryWorkChunk],
    *,
    repair: bool = False,
    aggressive: bool = False,
    rebalance: bool = False,
) -> list[dict[str, str]]:
    schema = SummarySections.model_json_schema()
    payload = [
        {
            "source_message_ids": chunk.message_ids,
            "summary": chunk.sections.model_dump(mode="json"),
        }
        for chunk in chunks
    ]
    strict = repair or aggressive or rebalance
    fact_limit = (
        DENSE_CHILD_TARGET_FACTS if rebalance else 45 if strict else MERGED_SUMMARY_MAX_FACTS
    )
    text_limit = (
        DENSE_CHILD_MAX_TEXT_CHARS
        if rebalance
        else 240
        if strict
        else MERGED_SUMMARY_MAX_TEXT_CHARS
    )
    evidence_limit = (
        DENSE_CHILD_MAX_EVIDENCE_IDS
        if rebalance
        else 3
        if strict
        else MERGED_SUMMARY_MAX_EVIDENCE_IDS
    )
    if rebalance:
        repair_note = "This input is too dense for a bounded parent merge; compact it first. "
    elif repair:
        repair_note = "A previous merge was unusable; compact more aggressively. "
    elif aggressive:
        repair_note = "The inputs are dense; compact aggressively. "
    else:
        repair_note = ""
    return [
        {
            "role": "system",
            "content": (
                repair_note
                + "Merge the supplied fiction-context summaries into one JSON object matching "
                "the schema. Return JSON only. Preserve the original evidence message IDs, "
                "retain conflicts as uncertainty, deduplicate equivalent facts, and never "
                "invent evidence or promote inference to canon. Hard compaction limits: "
                f"no more than {fact_limit} items total across all sections; each item's text "
                f"must be at most {text_limit} characters; cite at most {evidence_limit} "
                "evidence message IDs per item. Prioritize current state, established facts, "
                "unresolved threads, contradictions, and details needed for later "
                "continuity.\nSCHEMA:\n" + json.dumps(schema, separators=(",", ":"))
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


class ContextMaintainer:
    """Create one source-bound summary without touching narrative history."""

    def __init__(
        self,
        *,
        store: ContextSummaryStore,
        policy: ContextPolicy,
        counter: TokenCounter,
        configured_model: str,
        provider_id: str | None,
        model_id: str | None,
        generate: SummaryGeneratorCall,
    ) -> None:
        self.store = store
        self.policy = policy
        self.counter = counter
        self.configured_model = configured_model
        self.provider_id = provider_id
        self.model_id = model_id
        self.generate = generate

    async def maintain(
        self,
        session: ChatSession,
        source_messages: list[SessionMessage],
    ) -> ContextSummary:
        if not source_messages:
            raise ContextSummaryError("summary maintenance requires a non-empty source prefix")
        source = source_for(session, source_messages)
        chunks = _chunks(
            source_messages,
            max_tokens=self.policy.summary_chunk_tokens,
            counter=self.counter,
        )
        work = self.store.load_work(session.id)
        reusable = False
        if (
            work is not None
            and work.generator_model == self.configured_model
            and work.source.session_id == source.session_id
            and work.source.context_id == source.context_id
            and len(work.source.covered_message_ids) <= len(source_messages)
        ):
            covered_count = len(work.source.covered_message_ids)
            covered_prefix = source_messages[:covered_count]
            reusable = [
                item.id for item in covered_prefix
            ] == work.source.covered_message_ids and message_prefix_hash(
                covered_prefix
            ) == work.source.prefix_sha256
        if not reusable:
            work = SummaryWork(
                source=source,
                generator_model=self.configured_model,
                chunks=[],
                updated_at=utc_now(),
            )
        else:
            work = work.model_copy(update={"source": source, "updated_at": utc_now()})

        completed = {item.source_sha256: item for item in work.chunks}
        ordered: list[SummaryWorkChunk] = []
        for chunk in chunks:
            digest = message_prefix_hash(chunk)
            cached = completed.get(digest)
            if cached is not None and cached.message_ids == [item.id for item in chunk]:
                ordered.append(cached)
                continue
            prompt = summary_prompt(chunk)
            if self.counter.count_messages(prompt) > self.policy.application_tokens:
                raise ContextTooLarge("context-maintenance source prompt exceeds its input budget")
            result = await self.generate(prompt, self.configured_model)
            if self.counter.count_text(result.content) > self.policy.summary_max_tokens:
                raise ContextTooLarge("context-maintenance output exceeds the summary budget")
            sections = parse_summary_sections(result.content)
            unknown = sections.evidence_ids() - {item.id for item in chunk}
            if unknown:
                raise ContextSummaryError(
                    "context-maintenance output cited messages outside its source chunk"
                )
            item = SummaryWorkChunk(
                source_sha256=digest,
                message_ids=[message.id for message in chunk],
                sections=sections,
                usage=result.usage,
                receipt_id=result.receipt_id,
                provider_id=result.provider_id,
                model_id=result.model_id,
            )
            ordered.append(item)
            work = work.model_copy(update={"chunks": ordered, "updated_at": utc_now()})
            self.store.save_work(work)

        provider_id: str | None = None
        model_id: str | None = None
        completed_merges = {item.source_sha256: item for item in work.merges}
        saved_merges = list(work.merges)
        used_merges: list[SummaryWorkChunk] = []
        if len(ordered) == 1:
            sections = ordered[0].sections
        else:
            level = ordered
            while len(level) > 1:
                next_level: list[SummaryWorkChunk] = []
                for offset in range(0, len(level), 2):
                    group = level[offset : offset + 2]
                    if len(group) == 1:
                        next_level.append(group[0])
                        continue
                    message_ids = [message_id for item in group for message_id in item.message_ids]
                    digest_payload = "".join(item.source_sha256 for item in group)
                    digest = hashlib.sha256(digest_payload.encode("ascii")).hexdigest()
                    cached = completed_merges.get(digest)
                    if cached is not None and cached.message_ids == message_ids:
                        if cached.sections.evidence_ids() - set(message_ids):
                            raise ContextSummaryError(
                                "cached context summary merge cited messages outside "
                                "its source group"
                            )
                        used_merges.append(cached)
                        next_level.append(cached)
                        continue
                    input_facts = sum(
                        len(getattr(item.sections, name))
                        for item in group
                        for name in item.sections.__class__.model_fields
                    )
                    if input_facts > MERGED_SUMMARY_MAX_FACTS:
                        balanced_group: list[SummaryWorkChunk] = []
                        for child in group:
                            child_fact_count = sum(
                                len(getattr(child.sections, name))
                                for name in child.sections.__class__.model_fields
                            )
                            if child_fact_count <= DENSE_CHILD_MAX_FACTS:
                                balanced_group.append(child)
                                continue
                            child_digest = hashlib.sha256(
                                f"dense-child-v1:{child.source_sha256}".encode("ascii")
                            ).hexdigest()
                            cached_child = completed_merges.get(child_digest)
                            if (
                                cached_child is not None
                                and cached_child.message_ids == child.message_ids
                            ):
                                if cached_child.sections.evidence_ids() - set(child.message_ids):
                                    raise ContextSummaryError(
                                        "cached dense child cited messages outside its source"
                                    )
                                used_merges.append(cached_child)
                                balanced_group.append(cached_child)
                                continue
                            prompt = _merge_prompt([child], rebalance=True)
                            if self.counter.count_messages(prompt) > self.policy.application_tokens:
                                raise ContextTooLarge(
                                    "context-maintenance dense-child merge exceeds its input budget"
                                )
                            reduced = await self.generate(prompt, self.configured_model)
                            if (
                                self.counter.count_text(reduced.content)
                                > self.policy.summary_max_tokens
                            ):
                                raise ContextTooLarge(
                                    "dense-child context summary exceeds the summary budget"
                                )
                            reduced_sections = parse_summary_sections(reduced.content)
                            _validate_sections_with_limits(
                                reduced_sections,
                                max_facts=DENSE_CHILD_MAX_FACTS,
                                max_text_chars=DENSE_CHILD_MAX_TEXT_CHARS,
                                max_evidence_ids=DENSE_CHILD_MAX_EVIDENCE_IDS,
                            )
                            if reduced_sections.evidence_ids() - set(child.message_ids):
                                raise ContextSummaryError(
                                    "dense-child context summary cited messages outside its source"
                                )
                            reduced_item = SummaryWorkChunk(
                                source_sha256=child_digest,
                                message_ids=child.message_ids,
                                sections=reduced_sections,
                                usage=reduced.usage,
                                receipt_id=reduced.receipt_id,
                                provider_id=reduced.provider_id,
                                model_id=reduced.model_id,
                            )
                            completed_merges[child_digest] = reduced_item
                            saved_merges.append(reduced_item)
                            used_merges.append(reduced_item)
                            balanced_group.append(reduced_item)
                            work = work.model_copy(
                                update={"merges": saved_merges, "updated_at": utc_now()}
                            )
                            self.store.save_work(work)
                        group = balanced_group
                        digest_payload = "".join(item.source_sha256 for item in group)
                        digest = hashlib.sha256(digest_payload.encode("ascii")).hexdigest()
                        cached = completed_merges.get(digest)
                        if cached is not None and cached.message_ids == message_ids:
                            if cached.sections.evidence_ids() - set(message_ids):
                                raise ContextSummaryError(
                                    "cached balanced merge cited messages outside its source group"
                                )
                            used_merges.append(cached)
                            next_level.append(cached)
                            continue
                    merged: SummaryModelResult | None = None
                    merged_sections: SummarySections | None = None
                    input_facts = sum(
                        len(getattr(item.sections, name))
                        for item in group
                        for name in item.sections.__class__.model_fields
                    )
                    aggressive = input_facts > MERGED_SUMMARY_MAX_FACTS
                    merge_modes = (False,) if aggressive else (False, True)
                    for repair in merge_modes:
                        prompt = _merge_prompt(group, repair=repair, aggressive=aggressive)
                        if self.counter.count_messages(prompt) > self.policy.application_tokens:
                            raise ContextTooLarge(
                                "context-maintenance merge exceeds its input budget"
                            )
                        candidate = await self.generate(prompt, self.configured_model)
                        try:
                            if (
                                self.counter.count_text(candidate.content)
                                > self.policy.summary_max_tokens
                            ):
                                raise ContextTooLarge(
                                    "merged context summary exceeds the summary budget"
                                )
                            candidate_sections = parse_summary_sections(candidate.content)
                            _validate_merged_sections(candidate_sections)
                            if candidate_sections.evidence_ids() - set(message_ids):
                                raise ContextSummaryError(
                                    "merged context summary cited messages outside its source group"
                                )
                        except ContextSummaryError:
                            if repair or aggressive:
                                raise
                            continue
                        merged = candidate
                        merged_sections = candidate_sections
                        break
                    assert merged is not None and merged_sections is not None
                    item = SummaryWorkChunk(
                        source_sha256=digest,
                        message_ids=message_ids,
                        sections=merged_sections,
                        usage=merged.usage,
                        receipt_id=merged.receipt_id,
                        provider_id=merged.provider_id,
                        model_id=merged.model_id,
                    )
                    completed_merges[digest] = item
                    saved_merges.append(item)
                    used_merges.append(item)
                    next_level.append(item)
                    work = work.model_copy(update={"merges": saved_merges, "updated_at": utc_now()})
                    self.store.save_work(work)
                level = next_level
            sections = level[0].sections
            provider_id = level[0].provider_id
            model_id = level[0].model_id

        usage: dict[str, int] = {}
        for item in ordered:
            for name, value in item.usage.items():
                usage[name] = usage.get(name, 0) + value
        for item in used_merges:
            for name, value in item.usage.items():
                usage[name] = usage.get(name, 0) + value
        receipts = [item.receipt_id for item in ordered if item.receipt_id]
        receipts.extend(item.receipt_id for item in used_merges if item.receipt_id)
        work = work.model_copy(update={"merges": used_merges, "updated_at": utc_now()})
        self.store.save_work(work)
        summary = ContextSummary(
            source=source,
            generator=SummaryGenerator(
                configured_model=self.configured_model,
                provider_id=provider_id or self.provider_id,
                model_id=model_id or self.model_id,
                receipt_ids=receipts,
            ),
            created_at=utc_now(),
            usage=usage,
            sections=sections,
        )
        self.store.save(summary)
        return summary


def source_fingerprint(messages: list[SessionMessage]) -> str:
    """Opaque identifier for operational reports; never log source prose."""
    return hashlib.sha256(message_prefix_hash(messages).encode("ascii")).hexdigest()[:16]
