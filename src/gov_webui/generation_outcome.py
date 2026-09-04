# SPDX-License-Identifier: Apache-2.0
"""Typed outcomes at Marginalia's governed-generation boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class GenerationFailureKind(StrEnum):
    """Operational failure classes that must never become authored text."""

    TIMEOUT = "timeout"
    AUTHENTICATION = "authentication"
    PROVIDER_EXECUTION = "provider_execution"
    TRANSPORT = "transport"
    INVALID_RESULT = "invalid_result"
    STALE_CONTEXT = "stale_context"
    CONTEXT_MAINTENANCE = "context_maintenance"
    CONTEXT_TOO_LARGE = "context_too_large"
    SERVICE_MAINTENANCE = "service_maintenance"
    INTERNAL = "internal"


class InvalidGenerationResult(RuntimeError):
    """The provider/daemon returned no usable governed authored result."""


class ModelMismatchGenerationResult(InvalidGenerationResult):
    """The daemon substituted a model after explicit selection."""


@dataclass(frozen=True)
class AuthoredGeneration:
    outcome: Literal["authored"]
    content: str
    model: str
    usage: dict[str, int]
    receipt: dict[str, Any]
    footer: str | None = None


@dataclass(frozen=True)
class BlockedGeneration:
    outcome: Literal["blocked"]
    pending: dict[str, Any]
    model: str
    receipt: dict[str, Any]


@dataclass(frozen=True)
class FailedGeneration:
    outcome: Literal["failure"]
    kind: GenerationFailureKind
    message: str
    retryable: bool
    incident_id: str


GenerationOutcome = AuthoredGeneration | BlockedGeneration | FailedGeneration


def classify_daemon_result(
    result: Any, requested_model: str
) -> AuthoredGeneration | BlockedGeneration:
    """Convert an untyped daemon payload into a content-safe terminal outcome."""
    if not isinstance(result, dict):
        raise InvalidGenerationResult("governor returned a non-object generation result")

    receipt = result.get("receipt")
    if not isinstance(receipt, dict) or not isinstance(receipt.get("receipt_id"), str):
        raise InvalidGenerationResult("governor result omitted its authority receipt")

    model = result.get("model") or requested_model
    if not isinstance(model, str) or not model:
        raise InvalidGenerationResult("governor result omitted the generated model")

    pending = result.get("pending")
    if pending is not None:
        if not isinstance(pending, dict):
            raise InvalidGenerationResult("governor returned malformed pending state")
        return BlockedGeneration(outcome="blocked", pending=pending, model=model, receipt=receipt)

    content = result.get("content")
    if not isinstance(content, str) or not content.strip():
        raise InvalidGenerationResult("provider returned no usable authored content")

    raw_usage = result.get("usage") or {}
    if not isinstance(raw_usage, dict):
        raise InvalidGenerationResult("provider returned malformed usage data")
    usage: dict[str, int] = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw_usage.get(name, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InvalidGenerationResult("provider returned malformed usage data")
        usage[name] = value

    footer = result.get("footer")
    if footer is not None and not isinstance(footer, str):
        raise InvalidGenerationResult("governor returned a malformed status footer")

    return AuthoredGeneration(
        outcome="authored",
        content=content,
        model=model,
        usage=usage,
        receipt=receipt,
        footer=footer,
    )
