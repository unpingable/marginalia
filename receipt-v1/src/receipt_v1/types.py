# SPDX-License-Identifier: Apache-2.0
"""Receipt v1 types: enums and frozen dataclasses.

All types are immutable. Serialization produces dicts with only present fields
(no None values). Deserialization accepts dicts with optional fields omitted.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, fields
from typing import Any


# ── Enums ────────────────────────────────────────────────────────────────────

class Action(enum.Enum):
    """Policy decision action."""
    ALLOW = "allow"
    DENY = "deny"
    TRANSFORM = "transform"
    ESCALATE = "escalate"


class ExecutionStatus(enum.Enum):
    """Execution outcome status."""
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class EffectsConfidence(enum.Enum):
    """Confidence level for reported side effects."""
    NONE = "none"
    COARSE = "coarse"
    INSTRUMENTED = "instrumented"


class AuthContext(enum.Enum):
    """How actor identity was established."""
    STDIO = "stdio"
    OAUTH = "oauth"
    MTLS = "mtls"
    API_KEY = "api_key"
    NONE = "none"


class Transport(enum.Enum):
    """Tool origin transport."""
    STDIO = "stdio"
    HTTP = "http"


class PolicyOutcome(enum.Enum):
    """Outcome of a single policy step."""
    PASS = "pass"
    FAIL = "fail"
    TRANSFORM = "transform"
    ESCALATE = "escalate"
    SKIP = "skip"


# ── String safety ────────────────────────────────────────────────────────────

MAX_HUMAN_LEN = 256


def _sanitize_human(s: str | None) -> str | None:
    """Sanitize a human-readable string: single-line, max 256 chars."""
    if s is None:
        return None
    s = s.replace("\n", " ").replace("\r", " ")
    if len(s) > MAX_HUMAN_LEN:
        s = s[:MAX_HUMAN_LEN]
    return s


def _validate_nonempty(val: str | None, name: str) -> None:
    """Raise ValueError if val is empty string. None is allowed (means omit)."""
    if val is not None and val == "":
        raise ValueError(f"{name} must not be empty string; omit it instead")


# ── Dataclasses ──────────────────────────────────────────────────────────────

def _to_dict(obj: Any) -> Any:
    """Recursively convert dataclass to dict, omitting None values and empty ext."""
    if isinstance(obj, enum.Enum):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):
        d = {}
        for f in fields(obj):
            v = getattr(obj, f.name)
            if v is None:
                continue
            converted = _to_dict(v)
            # Omit empty ext
            if f.name == "ext" and isinstance(converted, dict) and not converted:
                continue
            d[f.name] = converted
        return d
    if isinstance(obj, (list, tuple)):
        return [_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items() if v is not None}
    return obj


@dataclass(frozen=True)
class Origin:
    """Where the tool lives (for aggregated/proxied setups)."""
    server_id: str
    transport: Transport
    instance: str | None = None

    def __post_init__(self) -> None:
        _validate_nonempty(self.instance, "origin.instance")

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class Actor:
    """Who initiated the tool call."""
    agent_id: str
    session_id: str
    runtime: str | None = None
    issuer: str | None = None
    auth_context: AuthContext | None = None

    def __post_init__(self) -> None:
        _validate_nonempty(self.runtime, "actor.runtime")
        _validate_nonempty(self.issuer, "actor.issuer")

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class Tool:
    """Tool call details."""
    tool_id: str
    args_hash: str
    tool_version: str | None = None
    call_id: str | None = None
    args_summary: str | None = None
    capability_asserted: str | None = None
    origin: Origin | None = None

    def __post_init__(self) -> None:
        _validate_nonempty(self.tool_version, "tool.tool_version")
        _validate_nonempty(self.call_id, "tool.call_id")
        _validate_nonempty(self.capability_asserted, "tool.capability_asserted")
        # Sanitize args_summary
        if self.args_summary is not None:
            object.__setattr__(self, "args_summary", _sanitize_human(self.args_summary))

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class PolicyStep:
    """A single step in the policy evaluation path."""
    id: str
    version: str
    outcome: PolicyOutcome

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class Budget:
    """Budget state after decision."""
    rate_remaining: int | None = None
    cost_remaining: int | None = None
    retries_remaining: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class Decision:
    """Policy decision."""
    action: Action
    reason_code: str
    reason_human: str | None = None
    policy_id: str | None = None
    policy_version: str | None = None
    policy_path: list[PolicyStep] | None = None
    transformed_args_hash: str | None = None
    budget: Budget | None = None

    def __post_init__(self) -> None:
        _validate_nonempty(self.policy_id, "decision.policy_id")
        _validate_nonempty(self.policy_version, "decision.policy_version")
        if self.reason_human is not None:
            object.__setattr__(self, "reason_human", _sanitize_human(self.reason_human))

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class Execution:
    """Execution outcome (present only when action=allow or action=transform)."""
    status: ExecutionStatus
    attempt: int = 1
    effect_summary: str | None = None
    side_effects: list[str] | None = None
    effects_confidence: EffectsConfidence | None = None
    result_hash: str | None = None
    result_summary: str | None = None
    duration_ms: int | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must be >= 0")
        # Sanitize human-readable fields
        if self.effect_summary is not None:
            object.__setattr__(self, "effect_summary", _sanitize_human(self.effect_summary))
        if self.result_summary is not None:
            object.__setattr__(self, "result_summary", _sanitize_human(self.result_summary))
        if self.error is not None:
            object.__setattr__(self, "error", _sanitize_human(self.error))
        # Enforce sorted + unique side_effects
        if self.side_effects is not None:
            deduped = sorted(set(self.side_effects))
            object.__setattr__(self, "side_effects", deduped)

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class Chain:
    """Chain linkage for receipt sequencing."""
    seq: int
    parent_receipt_id: str | None = None
    parent_receipt_hash: str | None = None

    def __post_init__(self) -> None:
        if self.seq < 1:
            raise ValueError("chain.seq must be >= 1")
        # For seq=1, parent fields should be absent
        if self.seq == 1 and (self.parent_receipt_id is not None or self.parent_receipt_hash is not None):
            raise ValueError("chain: seq=1 must not have parent_receipt_id or parent_receipt_hash")
        # For seq>1, both parent fields required
        if self.seq > 1:
            if self.parent_receipt_id is None or self.parent_receipt_hash is None:
                raise ValueError("chain: seq>1 requires both parent_receipt_id and parent_receipt_hash")

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class Provenance:
    """Receipt provenance and integrity metadata."""
    deployment_id: str
    instance_id: str
    governor_version: str
    hash_alg: str = "sha-256"
    hash_encoding: str = "hex"
    signature: str | None = None
    signature_alg: str | None = None
    signature_encoding: str | None = None
    signing_key_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class Receipt:
    """A complete Receipt v1 record."""
    receipt_id: str
    receipt_version: str
    receipt_hash: str
    chain: Chain
    timestamp_wall: str
    actor: Actor
    tool: Tool
    decision: Decision
    provenance: Provenance
    timestamp_mono: int | None = None
    execution: Execution | None = None
    ext: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Receipt:
        """Deserialize a dict into a Receipt.

        Reconstructs all nested dataclasses and enums from plain dict values.
        """
        # Actor
        actor_d = d["actor"]
        actor = Actor(
            agent_id=actor_d["agent_id"],
            session_id=actor_d["session_id"],
            runtime=actor_d.get("runtime"),
            issuer=actor_d.get("issuer"),
            auth_context=AuthContext(actor_d["auth_context"]) if "auth_context" in actor_d else None,
        )

        # Tool
        tool_d = d["tool"]
        origin = None
        if "origin" in tool_d:
            o = tool_d["origin"]
            origin = Origin(
                server_id=o["server_id"],
                transport=Transport(o["transport"]),
                instance=o.get("instance"),
            )
        tool = Tool(
            tool_id=tool_d["tool_id"],
            args_hash=tool_d["args_hash"],
            tool_version=tool_d.get("tool_version"),
            call_id=tool_d.get("call_id"),
            args_summary=tool_d.get("args_summary"),
            capability_asserted=tool_d.get("capability_asserted"),
            origin=origin,
        )

        # Decision
        dec_d = d["decision"]
        policy_path = None
        if "policy_path" in dec_d:
            policy_path = [
                PolicyStep(id=ps["id"], version=ps["version"], outcome=PolicyOutcome(ps["outcome"]))
                for ps in dec_d["policy_path"]
            ]
        budget = None
        if "budget" in dec_d:
            b = dec_d["budget"]
            budget = Budget(
                rate_remaining=b.get("rate_remaining"),
                cost_remaining=b.get("cost_remaining"),
                retries_remaining=b.get("retries_remaining"),
            )
        decision = Decision(
            action=Action(dec_d["action"]),
            reason_code=dec_d["reason_code"],
            reason_human=dec_d.get("reason_human"),
            policy_id=dec_d.get("policy_id"),
            policy_version=dec_d.get("policy_version"),
            policy_path=policy_path,
            transformed_args_hash=dec_d.get("transformed_args_hash"),
            budget=budget,
        )

        # Execution
        execution = None
        if "execution" in d:
            ex_d = d["execution"]
            execution = Execution(
                status=ExecutionStatus(ex_d["status"]),
                attempt=ex_d.get("attempt", 1),
                effect_summary=ex_d.get("effect_summary"),
                side_effects=ex_d.get("side_effects"),
                effects_confidence=EffectsConfidence(ex_d["effects_confidence"]) if "effects_confidence" in ex_d else None,
                result_hash=ex_d.get("result_hash"),
                result_summary=ex_d.get("result_summary"),
                duration_ms=ex_d.get("duration_ms"),
                error=ex_d.get("error"),
            )

        # Chain
        chain_d = d["chain"]
        chain = Chain(
            seq=chain_d["seq"],
            parent_receipt_id=chain_d.get("parent_receipt_id"),
            parent_receipt_hash=chain_d.get("parent_receipt_hash"),
        )

        # Provenance
        prov_d = d["provenance"]
        provenance = Provenance(
            deployment_id=prov_d["deployment_id"],
            instance_id=prov_d["instance_id"],
            governor_version=prov_d["governor_version"],
            hash_alg=prov_d.get("hash_alg", "sha-256"),
            hash_encoding=prov_d.get("hash_encoding", "hex"),
            signature=prov_d.get("signature"),
            signature_alg=prov_d.get("signature_alg"),
            signature_encoding=prov_d.get("signature_encoding"),
            signing_key_id=prov_d.get("signing_key_id"),
        )

        return Receipt(
            receipt_id=d["receipt_id"],
            receipt_version=d["receipt_version"],
            receipt_hash=d["receipt_hash"],
            chain=chain,
            timestamp_wall=d["timestamp_wall"],
            actor=actor,
            tool=tool,
            decision=decision,
            provenance=provenance,
            timestamp_mono=d.get("timestamp_mono"),
            execution=execution,
            ext=d.get("ext"),
        )
