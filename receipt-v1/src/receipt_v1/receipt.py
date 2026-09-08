# SPDX-License-Identifier: Apache-2.0
"""Receipt builder and chain management.

ReceiptBuilder: fluent API for constructing receipts with validation.
ReceiptChain: seq tracking, parent linking, automatic chaining.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from receipt_v1.canonical import _check_no_floats
from receipt_v1.canonical import args_hash as compute_args_hash
from receipt_v1.canonical import receipt_hash as compute_receipt_hash
from receipt_v1.canonical import result_hash as compute_result_hash
from receipt_v1.canonical import uuid7

# ext keys must be namespaced: "vendor.field" (lowercase + digits + dots/underscores)
_EXT_KEY_PATTERN = re.compile(r"^[a-z0-9]+\.[a-z0-9_.]+$")
from receipt_v1.types import (
    Action,
    Actor,
    Budget,
    Chain,
    Decision,
    EffectsConfidence,
    Execution,
    ExecutionStatus,
    PolicyOutcome,
    PolicyStep,
    Provenance,
    Receipt,
    Tool,
)


class ReceiptBuilder:
    """Fluent builder for Receipt v1.

    Computes hashes automatically. Enforces the no-floats and string-safety
    invariants from the spec.
    """

    def __init__(self) -> None:
        self._actor: Actor | None = None
        self._tool_id: str | None = None
        self._tool_version: str | None = None
        self._call_id: str | None = None
        self._args: Any = None
        self._args_summary: str | None = None
        self._capability_asserted: str | None = None
        self._origin: Any = None
        self._action: Action | None = None
        self._reason_code: str | None = None
        self._reason_human: str | None = None
        self._policy_id: str | None = None
        self._policy_version: str | None = None
        self._policy_path: list[PolicyStep] | None = None
        self._transformed_args: Any = None
        self._budget: Budget | None = None
        self._execution_status: ExecutionStatus | None = None
        self._attempt: int = 1
        self._effect_summary: str | None = None
        self._side_effects: list[str] | None = None
        self._effects_confidence: EffectsConfidence | None = None
        self._sanitized_result: Any = None
        self._result_summary: str | None = None
        self._duration_ms: int | None = None
        self._error: str | None = None
        self._provenance: Provenance | None = None
        self._timestamp_mono: int | None = None
        self._ext: dict[str, Any] | None = None
        self._receipt_id: str | None = None
        self._timestamp_wall: str | None = None

    def actor(self, actor: Actor) -> ReceiptBuilder:
        self._actor = actor
        return self

    def tool(self, tool_id: str, args: Any, *,
             tool_version: str | None = None,
             call_id: str | None = None,
             args_summary: str | None = None,
             capability_asserted: str | None = None,
             origin: Any = None) -> ReceiptBuilder:
        self._tool_id = tool_id
        self._args = args
        self._tool_version = tool_version
        self._call_id = call_id
        self._args_summary = args_summary
        self._capability_asserted = capability_asserted
        self._origin = origin
        return self

    def decision(self, action: Action, reason_code: str, *,
                 reason_human: str | None = None,
                 policy_id: str | None = None,
                 policy_version: str | None = None,
                 policy_path: list[PolicyStep] | None = None,
                 transformed_args: Any = None,
                 budget: Budget | None = None) -> ReceiptBuilder:
        self._action = action
        self._reason_code = reason_code
        self._reason_human = reason_human
        self._policy_id = policy_id
        self._policy_version = policy_version
        self._policy_path = policy_path
        self._transformed_args = transformed_args
        self._budget = budget
        return self

    def execution(self, status: ExecutionStatus, *,
                  attempt: int = 1,
                  effect_summary: str | None = None,
                  side_effects: list[str] | None = None,
                  effects_confidence: EffectsConfidence | None = None,
                  sanitized_result: Any = None,
                  result_summary: str | None = None,
                  duration_ms: int | None = None,
                  error: str | None = None) -> ReceiptBuilder:
        self._execution_status = status
        self._attempt = attempt
        self._effect_summary = effect_summary
        self._side_effects = side_effects
        self._effects_confidence = effects_confidence
        self._sanitized_result = sanitized_result
        self._result_summary = result_summary
        self._duration_ms = duration_ms
        self._error = error
        return self

    def provenance(self, provenance: Provenance) -> ReceiptBuilder:
        self._provenance = provenance
        return self

    def timestamp_mono(self, mono: int) -> ReceiptBuilder:
        self._timestamp_mono = mono
        return self

    def ext(self, ext: dict[str, Any]) -> ReceiptBuilder:
        """Set extension fields. Keys must be namespaced (e.g. 'vendor.field').
        Values must not contain floats (breaks cross-language hash parity).
        """
        for key in ext:
            if not _EXT_KEY_PATTERN.match(key):
                raise ValueError(
                    f"ext key {key!r} must be namespaced (e.g. 'vendor.field_name')"
                )
        _check_no_floats(ext, "ext")
        self._ext = ext
        return self

    def receipt_id(self, rid: str) -> ReceiptBuilder:
        self._receipt_id = rid
        return self

    def timestamp_wall(self, ts: str) -> ReceiptBuilder:
        self._timestamp_wall = ts
        return self

    def build(self, chain: Chain) -> Receipt:
        """Build the receipt with the given chain linkage.

        Computes args_hash, transformed_args_hash, result_hash, and receipt_hash
        automatically. Generates receipt_id (UUIDv7) and timestamp_wall if not set.
        """
        if self._actor is None:
            raise ValueError("actor is required")
        if self._tool_id is None or self._args is None:
            raise ValueError("tool (tool_id + args) is required")
        if self._action is None or self._reason_code is None:
            raise ValueError("decision (action + reason_code) is required")
        if self._provenance is None:
            raise ValueError("provenance is required")

        # Compute hashes
        a_hash = compute_args_hash(self._tool_id, self._args)

        ta_hash = None
        if self._transformed_args is not None:
            ta_hash = compute_args_hash(self._tool_id, self._transformed_args)

        r_hash = None
        if self._sanitized_result is not None:
            r_hash = compute_result_hash(self._tool_id, self._sanitized_result)

        # Build Tool
        tool = Tool(
            tool_id=self._tool_id,
            args_hash=a_hash,
            tool_version=self._tool_version,
            call_id=self._call_id,
            args_summary=self._args_summary,
            capability_asserted=self._capability_asserted,
            origin=self._origin,
        )

        # Build Decision
        decision = Decision(
            action=self._action,
            reason_code=self._reason_code,
            reason_human=self._reason_human,
            policy_id=self._policy_id,
            policy_version=self._policy_version,
            policy_path=self._policy_path,
            transformed_args_hash=ta_hash,
            budget=self._budget,
        )

        # Build Execution (only for allow/transform)
        execution = None
        if self._execution_status is not None:
            execution = Execution(
                status=self._execution_status,
                attempt=self._attempt,
                effect_summary=self._effect_summary,
                side_effects=self._side_effects,
                effects_confidence=self._effects_confidence,
                result_hash=r_hash,
                result_summary=self._result_summary,
                duration_ms=self._duration_ms,
                error=self._error,
            )

        # Generate ID and timestamp
        rid = self._receipt_id or uuid7()
        ts = self._timestamp_wall or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        # Build preliminary receipt dict for hashing (receipt_hash = "")
        prelim = Receipt(
            receipt_id=rid,
            receipt_version="1.0",
            receipt_hash="",  # placeholder
            chain=chain,
            timestamp_wall=ts,
            actor=self._actor,
            tool=tool,
            decision=decision,
            provenance=self._provenance,
            timestamp_mono=self._timestamp_mono,
            execution=execution,
            ext=self._ext,
        )

        # Compute receipt hash from dict
        prelim_dict = prelim.to_dict()
        r_hash_val = compute_receipt_hash(prelim_dict)

        # Return final receipt with correct hash
        return Receipt(
            receipt_id=rid,
            receipt_version="1.0",
            receipt_hash=r_hash_val,
            chain=chain,
            timestamp_wall=ts,
            actor=self._actor,
            tool=tool,
            decision=decision,
            provenance=self._provenance,
            timestamp_mono=self._timestamp_mono,
            execution=execution,
            ext=self._ext,
        )


class ReceiptChain:
    """Manages receipt chaining: seq tracking and parent linking.

    Usage:
        chain = ReceiptChain()
        r1 = builder1.build(chain.next())     # seq=1, no parent
        chain.append(r1)
        r2 = builder2.build(chain.next())     # seq=2, parent=r1
        chain.append(r2)
    """

    def __init__(self) -> None:
        self._seq: int = 0
        self._last_id: str | None = None
        self._last_hash: str | None = None

    @property
    def seq(self) -> int:
        """Current sequence number (last appended)."""
        return self._seq

    def next(self) -> Chain:
        """Get the Chain block for the next receipt."""
        next_seq = self._seq + 1
        if next_seq == 1:
            return Chain(seq=1)
        return Chain(
            seq=next_seq,
            parent_receipt_id=self._last_id,
            parent_receipt_hash=self._last_hash,
        )

    def append(self, receipt: Receipt) -> None:
        """Record a receipt as the latest in the chain."""
        expected_seq = self._seq + 1
        if receipt.chain.seq != expected_seq:
            raise ValueError(f"Expected seq {expected_seq}, got {receipt.chain.seq}")
        self._seq = receipt.chain.seq
        self._last_id = receipt.receipt_id
        self._last_hash = receipt.receipt_hash
