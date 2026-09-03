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


def _validate_merged_sections(sections: SummarySections) -> None:
    items = [item for name in sections.__class__.model_fields for item in getattr(sections, name)]
    longest_text = max((len(item.text) for item in items), default=0)
    most_evidence = max((len(item.evidence_message_ids) for item in items), default=0)
    if (
        len(items) > MERGED_SUMMARY_MAX_FACTS
        or longest_text > MERGED_SUMMARY_MAX_TEXT_CHARS
        or most_evidence > MERGED_SUMMARY_MAX_EVIDENCE_IDS
    ):
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


def _merge_prompt(chunks: list[SummaryWorkChunk], *, repair: bool = False) -> list[dict[str, str]]:
    schema = SummarySections.model_json_schema()
    payload = [
        {
            "source_message_ids": chunk.message_ids,
            "summary": chunk.sections.model_dump(mode="json"),
        }
        for chunk in chunks
    ]
    fact_limit = 45 if repair else MERGED_SUMMARY_MAX_FACTS
    text_limit = 240 if repair else MERGED_SUMMARY_MAX_TEXT_CHARS
    evidence_limit = 3 if repair else MERGED_SUMMARY_MAX_EVIDENCE_IDS
    repair_note = "A previous merge was unusable; compact more aggressively. " if repair else ""
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
                    merged: SummaryModelResult | None = None
                    merged_sections: SummarySections | None = None
                    for repair in (False, True):
                        prompt = _merge_prompt(group, repair=repair)
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
                            if not repair:
                                continue
                            raise
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
