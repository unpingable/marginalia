# SPDX-License-Identifier: Apache-2.0
"""Token-budgeted construction of fiction generation context."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, Sequence

from gov_webui.context_summary import (
    ContextMaintenanceRequired,
    ContextPolicy,
    ContextSummary,
    ContextTooLarge,
    render_summary,
)
from gov_webui.session_store import ChatSession, SessionMessage


class TokenCounter(Protocol):
    def count_text(self, text: str) -> int: ...
    def count_messages(self, messages: Sequence[dict[str, str]]) -> int: ...


class TiktokenCounter:
    """A real BPE token count with an explicit uncertainty margin."""

    def __init__(self, encoding_name: str = "o200k_base", safety_multiplier: float = 1.0):
        try:
            import tiktoken
        except ImportError as exc:  # pragma: no cover - deployment configuration failure
            raise RuntimeError("bounded context requires the tiktoken runtime dependency") from exc
        self.encoding = tiktoken.get_encoding(encoding_name)
        self.safety_multiplier = safety_multiplier

    def count_text(self, text: str) -> int:
        return math.ceil(len(self.encoding.encode(text)) * self.safety_multiplier)

    def count_messages(self, messages: Sequence[dict[str, str]]) -> int:
        raw = sum(
            len(self.encoding.encode(item["role"])) + len(self.encoding.encode(item["content"])) + 6
            for item in messages
        )
        return math.ceil(raw * self.safety_multiplier)


@dataclass(frozen=True)
class ContextMetrics:
    full_history_tokens: int
    application_tokens: int
    predicted_provider_tokens: int
    component_tokens: dict[str, int]
    recent_message_count: int
    summarized_message_count: int
    compacted: bool


@dataclass(frozen=True)
class BoundedGenerationContext:
    messages: list[dict[str, str]]
    metrics: ContextMetrics
    source_revision: int


def as_messages(items: Sequence[SessionMessage]) -> list[dict[str, str]]:
    return [{"role": item.role, "content": item.content} for item in items]


def pair_boundaries(messages: Sequence[SessionMessage]) -> list[int]:
    """Candidate prefix ends that never split a user/assistant exchange."""
    boundaries = [0]
    index = 0
    while index < len(messages):
        if (
            messages[index].role == "user"
            and index + 1 < len(messages)
            and messages[index + 1].role == "assistant"
        ):
            index += 2
        else:
            index += 1
        boundaries.append(index)
    return boundaries


def choose_summary_prefix(
    session: ChatSession,
    fixed_messages: list[dict[str, str]],
    pending_user: str,
    policy: ContextPolicy,
    counter: TokenCounter,
    additional_reserve_tokens: int = 0,
) -> list[SessionMessage]:
    """Choose the smallest old prefix whose replacement leaves a safe suffix."""
    mandatory = [*fixed_messages, {"role": "user", "content": pending_user}]
    mandatory_tokens = counter.count_messages(mandatory)
    allowance = (
        policy.application_tokens
        - mandatory_tokens
        - policy.summary_max_tokens
        - additional_reserve_tokens
    )
    if allowance < 0:
        raise ContextTooLarge("mandatory project context and prompt exceed the input budget")
    for boundary in pair_boundaries(session.messages):
        suffix = as_messages(session.messages[boundary:])
        if counter.count_messages(suffix) <= allowance:
            return session.messages[:boundary]
    raise ContextTooLarge("recent authored context cannot fit the input budget")


def build_generation_context(
    *,
    session: ChatSession,
    pending_user: str,
    fixed_messages: list[dict[str, str]],
    policy: ContextPolicy,
    counter: TokenCounter,
    summary: ContextSummary | None,
) -> BoundedGenerationContext:
    durable = as_messages(session.messages)
    pending = {"role": "user", "content": pending_user}
    full = [*fixed_messages, *durable, pending]
    full_tokens = counter.count_messages(full)

    if full_tokens <= policy.application_tokens:
        components = {
            "fixed": counter.count_messages(fixed_messages),
            "summary": 0,
            "recent": counter.count_messages(durable),
            "prompt": counter.count_messages([pending]),
        }
        return BoundedGenerationContext(
            messages=full,
            metrics=ContextMetrics(
                full_history_tokens=full_tokens,
                application_tokens=full_tokens,
                predicted_provider_tokens=full_tokens + policy.provider_overhead_tokens,
                component_tokens=components,
                recent_message_count=len(durable),
                summarized_message_count=0,
                compacted=False,
            ),
            source_revision=session.revision,
        )

    if summary is None:
        raise ContextMaintenanceRequired("long conversation has no valid derived summary")
    covered = len(summary.source.covered_message_ids)
    recent = durable[covered:]
    summary_message = render_summary(summary)
    bounded = [*fixed_messages, summary_message, *recent, pending]
    application_tokens = counter.count_messages(bounded)
    if application_tokens > policy.application_tokens:
        raise ContextMaintenanceRequired("derived summary does not cover enough older history")
    components = {
        "fixed": counter.count_messages(fixed_messages),
        "summary": counter.count_messages([summary_message]),
        "recent": counter.count_messages(recent),
        "prompt": counter.count_messages([pending]),
    }
    return BoundedGenerationContext(
        messages=bounded,
        metrics=ContextMetrics(
            full_history_tokens=full_tokens,
            application_tokens=application_tokens,
            predicted_provider_tokens=application_tokens + policy.provider_overhead_tokens,
            component_tokens=components,
            recent_message_count=len(recent),
            summarized_message_count=covered,
            compacted=True,
        ),
        source_revision=session.revision,
    )
