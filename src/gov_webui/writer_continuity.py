# SPDX-License-Identifier: Apache-2.0
"""
Continuity Enforcement — Closed-loop generation control.

Transforms the Governor from thermometer (observe and report) to thermostat
(observe, compare to setpoint, correct). Anchors define semantic constraints
(setpoints). Checker measures deviation (error signal). Correction ladder
applies escalating interventions (control input). Convergence executor
iterates until convergence or fails closed with evidence.

Pipeline:
    anchors (setpoints) → checker (error) → ladder (correction) → executor (loop)

Key invariants:
1. Anchors are generation constraints, NOT belief-graph nodes (cf. direction.Anchor)
2. Correction always rebuilds from ORIGINAL prompt (no prompt bloat)
3. Every generation attempt is logged (no silent retries)
4. Budget checked before each generation (fail-closed on exhaustion)
5. Ladder resets per call (no stale escalation state across calls)
6. Adapter is one-shot gate; ConvergenceExecutor is retry loop
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


# =============================================================================
# Enums
# =============================================================================


class AnchorType(str, Enum):
    """Classification of continuity anchors."""

    DEFINITION = "definition"  # Term/concept must be used consistently
    CANON = "canon"  # Established facts that must hold
    STYLE = "style"  # Writing style constraints
    PROHIBITION = "prohibition"  # Patterns/concepts that must not appear
    REQUIREMENT = "requirement"  # Patterns/concepts that must appear
    PERSONA = "persona"  # Voice/character constraints


class Severity(str, Enum):
    """How to handle anchor violations."""

    WARN = "warn"  # Log but accept
    CORRECT = "correct"  # Attempt correction
    REJECT = "reject"  # Hard failure


class RecommendedAction(str, Enum):
    """Action recommended by the checker based on violation analysis."""

    ACCEPT = "accept"
    SOFT_REPROMPT = "soft_reprompt"
    HARD_REPROMPT = "hard_reprompt"
    CONSTRAIN_DECODING = "constrain_decoding"
    ESCALATE = "escalate"
    FAIL_CLOSED = "fail_closed"


class CorrectionLevel(str, Enum):
    """Escalation levels in the correction ladder."""

    NONE = "none"
    SOFT_REPROMPT = "soft_reprompt"
    HARD_REPROMPT = "hard_reprompt"
    CONSTRAIN_DECODING = "constrain_decoding"
    REQUIRE_HITL = "require_hitl"
    FAIL_CLOSED = "fail_closed"


class ConstraintClass(str, Enum):
    """
    Classification of anchor constraints for Code Autopilot.

    INVARIANT: Never disabled by profile. Profile can modulate friction
               (prompts, retry budget) but cannot ignore the violation.
               Override mechanism required for exceptions.

    PREFERENCE: Profile controls enforcement level. Can be relaxed to
                ignore/warn/block based on profile settings.
    """

    INVARIANT = "invariant"  # Cannot be disabled by profile
    PREFERENCE = "preference"  # Profile can relax


# =============================================================================
# Dataclasses
# =============================================================================


@dataclass
class Anchor:
    """
    A semantic constraint (setpoint) for generation control.

    NOT related to direction.Anchor (belief graph node). This is a generation
    constraint: patterns that must or must not appear in output text.
    """

    id: str
    anchor_type: AnchorType
    description: str
    required_patterns: list[str] = field(default_factory=list)
    required_concepts: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)
    forbidden_concepts: list[str] = field(default_factory=list)
    severity: Severity = Severity.CORRECT
    custom_check: Callable[[str, "Anchor"], tuple[bool, list[str]]] | None = None
    source: str = "user"
    established_at: int | None = None
    constraint_class: ConstraintClass = ConstraintClass.PREFERENCE  # Code Autopilot

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "anchor_type": self.anchor_type.value,
            "description": self.description,
            "required_patterns": self.required_patterns,
            "required_concepts": self.required_concepts,
            "forbidden_patterns": self.forbidden_patterns,
            "forbidden_concepts": self.forbidden_concepts,
            "severity": self.severity.value,
            "source": self.source,
            "established_at": self.established_at,
            "constraint_class": self.constraint_class.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Anchor:
        return cls(
            id=data["id"],
            anchor_type=AnchorType(data["anchor_type"]),
            description=data["description"],
            required_patterns=data.get("required_patterns", []),
            required_concepts=data.get("required_concepts", []),
            forbidden_patterns=data.get("forbidden_patterns", []),
            forbidden_concepts=data.get("forbidden_concepts", []),
            severity=Severity(data.get("severity", "correct")),
            source=data.get("source", "user"),
            established_at=data.get("established_at"),
            constraint_class=ConstraintClass(data.get("constraint_class", "preference")),
        )

    def to_prompt_reminder(self) -> str:
        """Generate a prompt-friendly reminder of this anchor's constraints."""
        lines = [f"[{self.anchor_type.value.upper()}] {self.description}"]
        if self.required_patterns:
            lines.append(f"  REQUIRED: {', '.join(self.required_patterns)}")
        if self.required_concepts:
            lines.append(f"  MUST INCLUDE: {', '.join(self.required_concepts)}")
        if self.forbidden_patterns:
            lines.append(f"  FORBIDDEN: {', '.join(self.forbidden_patterns)}")
        if self.forbidden_concepts:
            lines.append(f"  MUST NOT INCLUDE: {', '.join(self.forbidden_concepts)}")
        return "\n".join(lines)


@dataclass
class Violation:
    """A specific continuity violation found during checking."""

    anchor_id: str
    anchor_type: AnchorType
    severity: Severity
    description: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "anchor_type": self.anchor_type.value,
            "severity": self.severity.value,
            "description": self.description,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Violation:
        return cls(
            anchor_id=data["anchor_id"],
            anchor_type=AnchorType(data["anchor_type"]),
            severity=Severity(data["severity"]),
            description=data["description"],
            evidence=data.get("evidence", []),
        )

    def __str__(self) -> str:
        return f"[{self.severity.value.upper()}] {self.anchor_id}: {self.description}"


@dataclass
class ContinuityReport:
    """Result of checking output against anchors."""

    passed: bool
    score: float  # 0.0 to 1.0: fraction of anchors with zero violations
    violations: list[Violation]
    recommended_action: RecommendedAction
    correction_context: str | None = None
    checked_anchors: int = 0
    check_time_ms: float = 0.0

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def has_critical(self) -> bool:
        return any(v.severity == Severity.REJECT for v in self.violations)

    def summary(self) -> str:
        if self.passed:
            return f"PASSED ({self.checked_anchors} anchors, score={self.score:.2f})"
        return (
            f"FAILED ({self.violation_count} violations, "
            f"score={self.score:.2f}, action={self.recommended_action.value})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "violations": [v.to_dict() for v in self.violations],
            "recommended_action": self.recommended_action.value,
            "correction_context": self.correction_context,
            "checked_anchors": self.checked_anchors,
            "check_time_ms": self.check_time_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContinuityReport:
        return cls(
            passed=data["passed"],
            score=data["score"],
            violations=[Violation.from_dict(v) for v in data.get("violations", [])],
            recommended_action=RecommendedAction(data["recommended_action"]),
            correction_context=data.get("correction_context"),
            checked_anchors=data.get("checked_anchors", 0),
            check_time_ms=data.get("check_time_ms", 0.0),
        )


@dataclass
class AttemptLog:
    """Record of a single generation attempt in the convergence loop."""

    attempt_number: int
    prompt_hash: str
    output_hash: str
    report: ContinuityReport
    correction_applied: str | None = None
    tokens_generated: int = 0
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_number": self.attempt_number,
            "prompt_hash": self.prompt_hash,
            "output_hash": self.output_hash,
            "report": self.report.to_dict(),
            "correction_applied": self.correction_applied,
            "tokens_generated": self.tokens_generated,
            "latency_ms": self.latency_ms,
        }


@dataclass
class CorrectionConfig:
    """Configuration for a single correction level."""

    level: CorrectionLevel
    max_retries_at_level: int = 2
    temperature_override: float | None = None
    force_json_output: bool = False
    require_human_approval: bool = False
    prepend_reminder: bool = False
    inject_constraints: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "max_retries_at_level": self.max_retries_at_level,
            "temperature_override": self.temperature_override,
            "force_json_output": self.force_json_output,
            "require_human_approval": self.require_human_approval,
            "prepend_reminder": self.prepend_reminder,
            "inject_constraints": self.inject_constraints,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorrectionConfig:
        return cls(
            level=CorrectionLevel(data["level"]),
            max_retries_at_level=data.get("max_retries_at_level", 2),
            temperature_override=data.get("temperature_override"),
            force_json_output=data.get("force_json_output", False),
            require_human_approval=data.get("require_human_approval", False),
            prepend_reminder=data.get("prepend_reminder", False),
            inject_constraints=data.get("inject_constraints", False),
        )


@dataclass
class ConvergenceBudget:
    """Resource limits for convergence attempts."""

    max_attempts: int = 5
    max_tokens: int = 10000
    max_time_ms: float = 30000.0
    stability_turns_required: int = 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "max_tokens": self.max_tokens,
            "max_time_ms": self.max_time_ms,
            "stability_turns_required": self.stability_turns_required,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConvergenceBudget:
        return cls(
            max_attempts=data.get("max_attempts", 5),
            max_tokens=data.get("max_tokens", 10000),
            max_time_ms=data.get("max_time_ms", 30000.0),
            stability_turns_required=data.get("stability_turns_required", 2),
        )


@dataclass
class ConvergenceResult:
    """Result of a convergence-seeking generation loop."""

    output: str
    converged: bool
    attempts: int
    final_report: ContinuityReport
    attempt_log: list[AttemptLog] = field(default_factory=list)
    final_prompt: str | None = None
    total_tokens: int = 0
    total_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "converged": self.converged,
            "attempts": self.attempts,
            "final_report": self.final_report.to_dict(),
            "attempt_log": [a.to_dict() for a in self.attempt_log],
            "final_prompt": self.final_prompt,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
        }


# =============================================================================
# AnchorRegistry
# =============================================================================


class AnchorRegistry:
    """
    Storage and retrieval for continuity anchors.

    Persistence path: .governor/continuity/anchors.json
    """

    def __init__(self) -> None:
        self._anchors: dict[str, Anchor] = {}
        self._by_type: dict[AnchorType, list[str]] = {t: [] for t in AnchorType}

    def register(self, anchor: Anchor) -> None:
        """Register an anchor. Overwrites if id already exists."""
        if anchor.id in self._anchors:
            self.unregister(anchor.id)
        self._anchors[anchor.id] = anchor
        self._by_type[anchor.anchor_type].append(anchor.id)

    def unregister(self, anchor_id: str) -> Anchor | None:
        """Remove an anchor by id. Returns the removed anchor or None."""
        anchor = self._anchors.pop(anchor_id, None)
        if anchor is not None:
            ids = self._by_type[anchor.anchor_type]
            if anchor_id in ids:
                ids.remove(anchor_id)
        return anchor

    def get(self, anchor_id: str) -> Anchor | None:
        """Get an anchor by id."""
        return self._anchors.get(anchor_id)

    def get_by_type(self, anchor_type: AnchorType) -> list[Anchor]:
        """Get all anchors of a given type."""
        return [self._anchors[aid] for aid in self._by_type[anchor_type] if aid in self._anchors]

    def all(self) -> list[Anchor]:
        """Get all registered anchors."""
        return list(self._anchors.values())

    def __len__(self) -> int:
        return len(self._anchors)

    def to_prompt_context(self) -> str:
        """Generate a prompt block summarizing all anchors."""
        if not self._anchors:
            return ""
        lines = ["ESTABLISHED DEFINITIONS AND CONSTRAINTS:"]
        for anchor in self._anchors.values():
            lines.append(anchor.to_prompt_reminder())
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchors": [a.to_dict() for a in self._anchors.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnchorRegistry:
        registry = cls()
        for a_data in data.get("anchors", []):
            registry.register(Anchor.from_dict(a_data))
        return registry

    def save(self, path: Path) -> None:
        """Save registry to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> AnchorRegistry:
        """Load registry from JSON file."""
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls.from_dict(data)


# =============================================================================
# ContinuityChecker
# =============================================================================


def _sha256(text: str) -> str:
    """Compute SHA-256 hex digest of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ContinuityChecker:
    """
    Measures deviation of output text from anchor constraints.

    Pattern matching v0 is lexical: regex-escaped substring search.
    Custom check callables take precedence when provided.
    """

    def __init__(
        self,
        case_sensitive: bool = False,
        receipt_system: Any | None = None,
    ) -> None:
        self._case_sensitive = case_sensitive
        self._receipt_system = receipt_system

    def check(self, output: str, anchors: list[Anchor]) -> ContinuityReport:
        """Check output text against a list of anchors."""
        start = time.monotonic()
        violations: list[Violation] = []
        anchors_passed = 0

        for anchor in anchors:
            anchor_violations = self._check_anchor(output, anchor)
            if not anchor_violations:
                anchors_passed += 1
            violations.extend(anchor_violations)

        total = len(anchors)
        score = anchors_passed / total if total > 0 else 1.0
        passed = len(violations) == 0

        action = self._recommend_action(violations)
        correction_context = (
            self._build_correction_context(violations, anchors) if not passed else None
        )

        elapsed_ms = (time.monotonic() - start) * 1000.0

        report = ContinuityReport(
            passed=passed,
            score=score,
            violations=violations,
            recommended_action=action,
            correction_context=correction_context,
            checked_anchors=total,
            check_time_ms=elapsed_ms,
        )

        self._emit_receipt(output, anchors, report)

        return report

    def _emit_receipt(
        self,
        output: str,
        anchors: list[Anchor],
        report: ContinuityReport,
    ) -> None:
        """Emit a gate receipt for this continuity check."""
        if self._receipt_system is None:
            return

        verdict = "pass" if report.passed else ("block" if report.has_critical else "warn")

        evidence_bundle = {
            "violations": [v.to_dict() for v in report.violations],
            "score": report.score,
            "checked_anchors": report.checked_anchors,
            "recommended_action": report.recommended_action.value,
        }

        gate_config = {
            "anchor_ids": [a.id for a in anchors],
            "case_sensitive": self._case_sensitive,
        }

        try:
            self._receipt_system.emit(
                gate="continuity_checker",
                verdict=verdict,
                subject_kind="text",
                subject_bytes=output.encode("utf-8"),
                evidence_bundle=evidence_bundle,
                gate_config=gate_config,
            )
        except Exception:
            pass  # fail-open on receipt emission

    def _check_anchor(self, output: str, anchor: Anchor) -> list[Violation]:
        """Check a single anchor against output. Returns violations."""
        # Custom check takes precedence
        if anchor.custom_check is not None:
            try:
                passed, evidence = anchor.custom_check(output, anchor)
            except Exception as e:
                return [
                    Violation(
                        anchor_id=anchor.id,
                        anchor_type=anchor.anchor_type,
                        severity=anchor.severity,
                        description=f"Custom check error: {e}",
                        evidence=[],
                    )
                ]
            if not passed:
                return [
                    Violation(
                        anchor_id=anchor.id,
                        anchor_type=anchor.anchor_type,
                        severity=anchor.severity,
                        description=f"Custom check failed for '{anchor.description}'",
                        evidence=evidence,
                    )
                ]
            return []

        violations: list[Violation] = []
        flags = 0 if self._case_sensitive else re.IGNORECASE

        # Check forbidden patterns
        for pattern in anchor.forbidden_patterns:
            escaped = re.escape(pattern)
            if re.search(escaped, output, flags):
                violations.append(
                    Violation(
                        anchor_id=anchor.id,
                        anchor_type=anchor.anchor_type,
                        severity=anchor.severity,
                        description=f"Forbidden pattern found: '{pattern}'",
                        evidence=[f"Pattern '{pattern}' matched in output"],
                    )
                )

        # Check forbidden concepts
        for concept in anchor.forbidden_concepts:
            escaped = re.escape(concept)
            if re.search(escaped, output, flags):
                violations.append(
                    Violation(
                        anchor_id=anchor.id,
                        anchor_type=anchor.anchor_type,
                        severity=anchor.severity,
                        description=f"Forbidden concept found: '{concept}'",
                        evidence=[f"Concept '{concept}' matched in output"],
                    )
                )

        # Check required patterns
        for pattern in anchor.required_patterns:
            escaped = re.escape(pattern)
            if not re.search(escaped, output, flags):
                violations.append(
                    Violation(
                        anchor_id=anchor.id,
                        anchor_type=anchor.anchor_type,
                        severity=anchor.severity,
                        description=f"Required pattern missing: '{pattern}'",
                        evidence=[f"Pattern '{pattern}' not found in output"],
                    )
                )

        # Check required concepts
        for concept in anchor.required_concepts:
            escaped = re.escape(concept)
            if not re.search(escaped, output, flags):
                violations.append(
                    Violation(
                        anchor_id=anchor.id,
                        anchor_type=anchor.anchor_type,
                        severity=anchor.severity,
                        description=f"Required concept missing: '{concept}'",
                        evidence=[f"Concept '{concept}' not found in output"],
                    )
                )

        return violations

    def _recommend_action(self, violations: list[Violation]) -> RecommendedAction:
        """Determine recommended action based on violations."""
        if not violations:
            return RecommendedAction.ACCEPT

        reject_count = sum(1 for v in violations if v.severity == Severity.REJECT)
        correct_count = sum(1 for v in violations if v.severity == Severity.CORRECT)
        warn_count = sum(1 for v in violations if v.severity == Severity.WARN)

        if reject_count > 0:
            return RecommendedAction.HARD_REPROMPT
        if correct_count > 2:
            return RecommendedAction.HARD_REPROMPT
        if correct_count >= 1:
            return RecommendedAction.SOFT_REPROMPT
        # Only warnings
        return RecommendedAction.ACCEPT

    def _build_correction_context(
        self, violations: list[Violation], anchors: list[Anchor]
    ) -> str | None:
        """Build a correction context string from violated anchors."""
        if not violations:
            return None

        # Collect unique violated anchor ids
        violated_ids = {v.anchor_id for v in violations}
        anchor_map = {a.id: a for a in anchors}

        lines = ["CORRECTION REQUIRED — the following constraints were violated:"]
        for aid in violated_ids:
            anchor = anchor_map.get(aid)
            if anchor:
                lines.append(anchor.to_prompt_reminder())
            # Add specific violations
            for v in violations:
                if v.anchor_id == aid:
                    lines.append(f"  >> {v.description}")

        return "\n".join(lines)


# =============================================================================
# CorrectionLadder
# =============================================================================


DEFAULT_LADDER: list[CorrectionConfig] = [
    CorrectionConfig(
        level=CorrectionLevel.SOFT_REPROMPT,
        max_retries_at_level=2,
        prepend_reminder=True,
    ),
    CorrectionConfig(
        level=CorrectionLevel.HARD_REPROMPT,
        max_retries_at_level=2,
        prepend_reminder=True,
        inject_constraints=True,
    ),
    CorrectionConfig(
        level=CorrectionLevel.CONSTRAIN_DECODING,
        max_retries_at_level=1,
        temperature_override=0.3,
        prepend_reminder=True,
        inject_constraints=True,
    ),
    CorrectionConfig(
        level=CorrectionLevel.REQUIRE_HITL,
        max_retries_at_level=1,
        require_human_approval=True,
    ),
    CorrectionConfig(
        level=CorrectionLevel.FAIL_CLOSED,
        max_retries_at_level=0,
    ),
]


class CorrectionLadder:
    """
    Escalating correction strategy for generation control.

    Starts at the lowest level and escalates when retries are exhausted.
    """

    def __init__(self, configs: list[CorrectionConfig] | None = None) -> None:
        self._configs = configs if configs is not None else list(DEFAULT_LADDER)
        self._level_index = 0
        self._retries_at_current = 0

    @property
    def current_level(self) -> CorrectionLevel:
        if self._level_index >= len(self._configs):
            return CorrectionLevel.FAIL_CLOSED
        return self._configs[self._level_index].level

    @property
    def current_config(self) -> CorrectionConfig:
        if self._level_index >= len(self._configs):
            return CorrectionConfig(level=CorrectionLevel.FAIL_CLOSED, max_retries_at_level=0)
        return self._configs[self._level_index]

    def should_escalate(self) -> bool:
        """Check if retries at current level are exhausted."""
        config = self.current_config
        return self._retries_at_current >= config.max_retries_at_level

    def escalate(self) -> CorrectionLevel:
        """Advance to the next escalation level. Returns the new level."""
        self._level_index += 1
        self._retries_at_current = 0
        return self.current_level

    def record_retry(self) -> None:
        """Record a retry at the current level."""
        self._retries_at_current += 1

    def reset(self) -> None:
        """Reset the ladder to the initial state."""
        self._level_index = 0
        self._retries_at_current = 0

    def is_exhausted(self) -> bool:
        """Check if all correction levels have been exhausted."""
        return self._level_index >= len(self._configs)

    def apply_correction(
        self, original_prompt: str, correction_context: str | None, anchors: list[Anchor]
    ) -> str:
        """
        Build a corrected prompt from the ORIGINAL prompt (never from previous output).

        This prevents prompt bloat from accumulated correction prefixes.
        """
        config = self.current_config
        parts: list[str] = []

        if config.prepend_reminder and correction_context:
            parts.append(correction_context)
            parts.append("")  # blank line separator

        if config.inject_constraints:
            constraint_lines = []
            for anchor in anchors:
                if anchor.forbidden_patterns or anchor.forbidden_concepts:
                    for p in anchor.forbidden_patterns:
                        constraint_lines.append(f"FORBIDDEN: {p}")
                    for c in anchor.forbidden_concepts:
                        constraint_lines.append(f"FORBIDDEN: {c}")
                if anchor.required_patterns or anchor.required_concepts:
                    for p in anchor.required_patterns:
                        constraint_lines.append(f"REQUIRED: {p}")
                    for c in anchor.required_concepts:
                        constraint_lines.append(f"REQUIRED: {c}")
            if constraint_lines:
                parts.append("CONSTRAINTS:")
                parts.extend(constraint_lines)
                parts.append("")  # blank line separator

        parts.append(original_prompt)
        return "\n".join(parts)

    def get_generation_params(self) -> dict[str, Any]:
        """Get generation parameters for the current correction level."""
        config = self.current_config
        params: dict[str, Any] = {}
        if config.temperature_override is not None:
            params["temperature"] = config.temperature_override
        if config.force_json_output:
            params["response_format"] = "json"
        return params


# =============================================================================
# GenerationProvider protocol
# =============================================================================


@runtime_checkable
class GenerationProvider(Protocol):
    """
    Protocol for text generation backends.

    Sync-only. Users wrap async backends if needed.
    No external dependencies — any callable with this signature works.
    """

    def generate(self, prompt: str, **kwargs: Any) -> str: ...


# =============================================================================
# ConvergenceExecutor
# =============================================================================


class ConvergenceExecutor:
    """
    Iterates generation until output satisfies all anchors or fails closed.

    The loop:
    1. Generate via provider
    2. Check via checker
    3. Log attempt
    4. If passed → return converged result
    5. Escalate correction level if retries exhausted
    6. Apply correction from ORIGINAL prompt
    7. Repeat until budget exhausted or ladder exhausted
    """

    def __init__(
        self,
        provider: GenerationProvider,
        checker: ContinuityChecker | None = None,
        ladder: CorrectionLadder | None = None,
        budget: ConvergenceBudget | None = None,
        collector: Any | None = None,
        mode: str = "",
    ) -> None:
        self._provider = provider
        self._checker = checker or ContinuityChecker()
        self._ladder = ladder or CorrectionLadder()
        self._budget = budget or ConvergenceBudget()
        self._collector = collector
        self._mode = mode

    def _compute_anchors_hash(self, anchors: list[Anchor]) -> str:
        """SHA256[:16] of sorted anchor IDs."""
        ids_str = ",".join(sorted(a.id for a in anchors))
        return hashlib.sha256(ids_str.encode("utf-8")).hexdigest()[:16]

    def _compute_error_by_anchor(self, report: ContinuityReport) -> dict[str, float]:
        """Violation count per anchor from a report."""
        counts: dict[str, float] = {}
        for v in report.violations:
            counts[v.anchor_id] = counts.get(v.anchor_id, 0.0) + 1.0
        return counts

    def _compute_error_total(self, report: ContinuityReport) -> float:
        """Error total = 1.0 - score, rounded."""
        return round(1.0 - report.score, 6)

    def _violations_to_dicts(self, violations: list[Violation]) -> list[dict[str, str]]:
        """Convert violations to telemetry-friendly dicts."""
        return [
            {
                "anchor_id": v.anchor_id,
                "severity": v.severity.value,
                "description": v.description,
            }
            for v in violations
        ]

    def _emit_trace(
        self,
        run_id: str,
        anchors_hash: str,
        attempt: int,
        report: ContinuityReport,
        prev_report: ContinuityReport | None,
        action: str,
        action_params: dict[str, Any],
        tokens: int,
        latency_ms: float,
        prompt_hash: str,
    ) -> None:
        """Emit a continuity trace event. Swallows all exceptions."""
        if self._collector is None:
            return
        try:
            error_total = self._compute_error_total(report)
            error_by_anchor = self._compute_error_by_anchor(report)
            delta_total: float | None = None
            delta_by_anchor: dict[str, float] = {}
            if prev_report is not None:
                prev_error = self._compute_error_total(prev_report)
                delta_total = round(error_total - prev_error, 6)
                prev_eba = self._compute_error_by_anchor(prev_report)
                all_aids = set(error_by_anchor) | set(prev_eba)
                delta_by_anchor = {
                    aid: round(error_by_anchor.get(aid, 0.0) - prev_eba.get(aid, 0.0), 6)
                    for aid in all_aids
                }
            self._collector.record_continuity_trace(
                run_id=run_id,
                mode=self._mode,
                attempt=attempt,
                error_total=error_total,
                error_by_anchor=error_by_anchor,
                violations=self._violations_to_dicts(report.violations),
                action=action,
                action_params=action_params,
                delta_total=delta_total,
                delta_by_anchor=delta_by_anchor,
                tokens=tokens,
                latency_ms=latency_ms,
                prompt_hash=prompt_hash,
                anchors_hash=anchors_hash,
            )
        except Exception:
            pass

    def _emit_result(
        self,
        run_id: str,
        anchors_hash: str,
        converged: bool,
        attempt_log: list[AttemptLog],
        final_report: ContinuityReport,
        total_tokens: int,
        total_latency_ms: float,
    ) -> None:
        """Emit a continuity result event. Swallows all exceptions."""
        if self._collector is None:
            return
        try:
            # Determine final_status
            if converged:
                final_status = "ACCEPTED"
            elif self._ladder.current_level == CorrectionLevel.REQUIRE_HITL:
                final_status = "ESCALATED"
            else:
                final_status = "REFUSED"

            # Build action_path
            action_path: list[str] = []
            for log in attempt_log:
                action_path.append(log.correction_applied or "none")

            # Compute stability diagnostics from attempt_log
            errors: list[float] = []
            error_maps: list[dict[str, float]] = []
            for log in attempt_log:
                errors.append(self._compute_error_total(log.report))
                error_maps.append(self._compute_error_by_anchor(log.report))

            # Monotone: error decreased every step (no increase > 1e-6)
            monotone = True
            if len(errors) > 1:
                for i in range(1, len(errors)):
                    if errors[i] - errors[i - 1] > 1e-6:
                        monotone = False
                        break

            # Oscillation: >=2 sign flips in consecutive deltas
            sign_flips = 0
            if len(errors) > 2:
                deltas = [errors[i] - errors[i - 1] for i in range(1, len(errors))]
                for i in range(1, len(deltas)):
                    if deltas[i] * deltas[i - 1] < 0:
                        sign_flips += 1
            oscillation_detected = sign_flips >= 2

            # Deadzone: actions where |delta| < 1e-6
            deadzone_actions: list[str] = []
            if len(errors) > 1:
                for i in range(1, len(errors)):
                    if abs(errors[i] - errors[i - 1]) < 1e-6:
                        deadzone_actions.append(action_path[i] if i < len(action_path) else "none")

            # Interference: anchor A violations decrease while B increases
            interference_edges: list[dict[str, str]] = []
            if len(error_maps) > 1:
                for i in range(1, len(error_maps)):
                    prev_m = error_maps[i - 1]
                    curr_m = error_maps[i]
                    all_aids = set(prev_m) | set(curr_m)
                    decreasing: list[str] = []
                    increasing: list[str] = []
                    for aid in all_aids:
                        d = curr_m.get(aid, 0.0) - prev_m.get(aid, 0.0)
                        if d < -1e-6:
                            decreasing.append(aid)
                        elif d > 1e-6:
                            increasing.append(aid)
                    for dec_aid in decreasing:
                        for inc_aid in increasing:
                            interference_edges.append(
                                {
                                    "from_anchor": dec_aid,
                                    "to_anchor": inc_aid,
                                }
                            )

            residual_error_total = self._compute_error_total(final_report)
            residual_error_by_anchor = self._compute_error_by_anchor(final_report)

            self._collector.record_continuity_result(
                run_id=run_id,
                mode=self._mode,
                attempts=len(attempt_log),
                final_status=final_status,
                residual_error_total=residual_error_total,
                residual_error_by_anchor=residual_error_by_anchor,
                action_path=action_path,
                total_tokens=total_tokens,
                total_latency_ms=total_latency_ms,
                monotone=monotone,
                oscillation_detected=oscillation_detected,
                deadzone_actions=deadzone_actions,
                interference_edges=interference_edges,
                anchors_hash=anchors_hash,
            )
        except Exception:
            pass

    def converge_generate(
        self,
        prompt: str,
        anchors: list[Anchor],
        **generation_kwargs: Any,
    ) -> ConvergenceResult:
        """
        Generate text, checking against anchors, with escalating correction.

        Returns ConvergenceResult with converged=True if output satisfies all
        anchors, or converged=False with the best attempt and full audit log.
        """
        # Reset ladder for this call (no stale state)
        self._ladder.reset()

        attempt_log: list[AttemptLog] = []
        best_output = ""
        best_score = -1.0
        best_report = ContinuityReport(
            passed=False,
            score=0.0,
            violations=[],
            recommended_action=RecommendedAction.FAIL_CLOSED,
        )

        total_tokens = 0
        total_latency_ms = 0.0
        current_prompt = prompt
        attempt = 0
        start_time = time.monotonic()

        # Telemetry identifiers (computed once per run)
        run_id = uuid.uuid4().hex[:12]
        anchors_hash = self._compute_anchors_hash(anchors)

        while attempt < self._budget.max_attempts:
            # Budget checks
            elapsed_ms = (time.monotonic() - start_time) * 1000.0
            if elapsed_ms >= self._budget.max_time_ms:
                break
            if total_tokens >= self._budget.max_tokens:
                break

            # Generate
            gen_params = {**generation_kwargs, **self._ladder.get_generation_params()}
            gen_start = time.monotonic()
            try:
                output = self._provider.generate(current_prompt, **gen_params)
            except Exception as e:
                # Generation failure — log and continue
                report = ContinuityReport(
                    passed=False,
                    score=0.0,
                    violations=[
                        Violation(
                            anchor_id="__system__",
                            anchor_type=AnchorType.REQUIREMENT,
                            severity=Severity.REJECT,
                            description=f"Generation error: {e}",
                        )
                    ],
                    recommended_action=RecommendedAction.FAIL_CLOSED,
                )
                gen_latency = (time.monotonic() - gen_start) * 1000.0
                attempt_log.append(
                    AttemptLog(
                        attempt_number=attempt,
                        prompt_hash=_sha256(current_prompt),
                        output_hash="",
                        report=report,
                        correction_applied=self._ladder.current_level.value,
                        latency_ms=gen_latency,
                    )
                )
                # Emit trace for generation error
                prev_report = attempt_log[-2].report if len(attempt_log) > 1 else None
                self._emit_trace(
                    run_id=run_id,
                    anchors_hash=anchors_hash,
                    attempt=attempt,
                    report=report,
                    prev_report=prev_report,
                    action=self._ladder.current_level.value if attempt > 0 else "none",
                    action_params=self._ladder.get_generation_params(),
                    tokens=0,
                    latency_ms=gen_latency,
                    prompt_hash=_sha256(current_prompt)[:16],
                )
                total_latency_ms += gen_latency
                attempt += 1
                self._ladder.record_retry()
                if self._ladder.should_escalate():
                    self._ladder.escalate()
                    if self._ladder.is_exhausted():
                        break
                # Re-apply correction from original prompt
                correction_ctx = report.correction_context
                current_prompt = self._ladder.apply_correction(prompt, correction_ctx, anchors)
                continue

            gen_latency = (time.monotonic() - gen_start) * 1000.0
            total_latency_ms += gen_latency
            tokens_est = len(output.split())  # rough estimate
            total_tokens += tokens_est

            # Check
            report = self._checker.check(output, anchors)

            # Log
            attempt_log.append(
                AttemptLog(
                    attempt_number=attempt,
                    prompt_hash=_sha256(current_prompt),
                    output_hash=_sha256(output),
                    report=report,
                    correction_applied=self._ladder.current_level.value if attempt > 0 else None,
                    tokens_generated=tokens_est,
                    latency_ms=gen_latency,
                )
            )

            # Emit telemetry trace
            prev_report = attempt_log[-2].report if len(attempt_log) > 1 else None
            self._emit_trace(
                run_id=run_id,
                anchors_hash=anchors_hash,
                attempt=attempt,
                report=report,
                prev_report=prev_report,
                action=self._ladder.current_level.value if attempt > 0 else "none",
                action_params=self._ladder.get_generation_params() if attempt > 0 else {},
                tokens=tokens_est,
                latency_ms=gen_latency,
                prompt_hash=_sha256(current_prompt)[:16],
            )

            # Track best
            if report.score > best_score:
                best_score = report.score
                best_output = output
                best_report = report

            # Converged?
            if report.passed:
                self._emit_result(
                    run_id=run_id,
                    anchors_hash=anchors_hash,
                    converged=True,
                    attempt_log=attempt_log,
                    final_report=report,
                    total_tokens=total_tokens,
                    total_latency_ms=total_latency_ms,
                )
                return ConvergenceResult(
                    output=output,
                    converged=True,
                    attempts=attempt + 1,
                    final_report=report,
                    attempt_log=attempt_log,
                    final_prompt=current_prompt,
                    total_tokens=total_tokens,
                    total_latency_ms=total_latency_ms,
                )

            # Escalate
            attempt += 1
            self._ladder.record_retry()
            if self._ladder.should_escalate():
                self._ladder.escalate()
                if self._ladder.is_exhausted():
                    break

            # Apply correction from ORIGINAL prompt
            current_prompt = self._ladder.apply_correction(
                prompt, report.correction_context, anchors
            )

        # Emit result telemetry before fail-closed return
        self._emit_result(
            run_id=run_id,
            anchors_hash=anchors_hash,
            converged=False,
            attempt_log=attempt_log,
            final_report=best_report,
            total_tokens=total_tokens,
            total_latency_ms=total_latency_ms,
        )

        # Fail-closed: return best attempt with evidence
        return ConvergenceResult(
            output=best_output,
            converged=False,
            attempts=len(attempt_log),
            final_report=best_report,
            attempt_log=attempt_log,
            final_prompt=current_prompt if attempt_log else prompt,
            total_tokens=total_tokens,
            total_latency_ms=total_latency_ms,
        )


# =============================================================================
# Convenience functions
# =============================================================================


def create_registry(governor_dir: Path | None = None) -> AnchorRegistry:
    """Create or load an AnchorRegistry from the governor directory."""
    if governor_dir is not None:
        path = governor_dir / "continuity" / "anchors.json"
        if path.exists():
            return AnchorRegistry.load(path)
    return AnchorRegistry()


def create_checker(case_sensitive: bool = False) -> ContinuityChecker:
    """Create a ContinuityChecker with the given settings."""
    return ContinuityChecker(case_sensitive=case_sensitive)


def create_ladder(ladder: list[CorrectionConfig] | None = None) -> CorrectionLadder:
    """Create a CorrectionLadder with custom or default configs."""
    return CorrectionLadder(configs=ladder)


def create_convergence_executor(
    provider: GenerationProvider,
    checker: ContinuityChecker | None = None,
    ladder: CorrectionLadder | None = None,
    budget: ConvergenceBudget | None = None,
    collector: Any | None = None,
    mode: str = "",
) -> ConvergenceExecutor:
    """Create a ConvergenceExecutor with the given components."""
    return ConvergenceExecutor(
        provider=provider,
        checker=checker,
        ladder=ladder,
        budget=budget,
        collector=collector,
        mode=mode,
    )


def check_output(output: str, anchors: list[Anchor]) -> ContinuityReport:
    """One-shot convenience: check output against anchors."""
    checker = ContinuityChecker()
    return checker.check(output, anchors)


# =============================================================================
# Code Autopilot Integration
# =============================================================================


@dataclass
class EnforcementAction:
    """
    Result of resolve_enforcement: what action to take for a violation.

    Attributes:
        action: The enforcement action (ignore/warn/block)
        override_available: Whether an override can be created for this violation
        reason: Why this action was chosen
    """

    action: str  # "ignore" | "warn" | "block"
    override_available: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "override_available": self.override_available,
            "reason": self.reason,
        }


def resolve_enforcement(
    violation: Violation,
    anchor: Anchor,
    profile_violation_default: str = "warn",
    invariant_floor: str = "warn",
) -> EnforcementAction:
    """
    Determine enforcement action based on constraint class and profile.

    For Code Autopilot integration. Maps anchor constraint class to
    enforcement action, respecting the hierarchy:
    - Invariants: cannot be disabled by profile, only modulated
    - Preferences: profile controls enforcement level

    Args:
        violation: The violation that occurred
        anchor: The anchor that was violated
        profile_violation_default: Profile's default violation handling
        invariant_floor: Minimum enforcement level for invariants

    Returns:
        EnforcementAction with action, override_available, and reason
    """
    # Map string actions to severity for comparison
    action_severity = {"ignore": 0, "warn": 1, "block": 2}

    if anchor.constraint_class == ConstraintClass.INVARIANT:
        # Invariant: profile cannot disable, only modulate friction
        # Take the higher of floor and profile default
        floor_sev = action_severity.get(invariant_floor, 1)
        profile_sev = action_severity.get(profile_violation_default, 1)
        effective_sev = max(floor_sev, profile_sev)

        # Convert back to action string
        for action, sev in action_severity.items():
            if sev == effective_sev:
                return EnforcementAction(
                    action=action,
                    override_available=True,  # Override available for invariants
                    reason=f"Invariant constraint (floor={invariant_floor}, profile={profile_violation_default})",
                )

        # Default to warn if something goes wrong
        return EnforcementAction(
            action="warn",
            override_available=True,
            reason="Invariant constraint (default fallback)",
        )

    else:
        # Preference: profile controls entirely
        return EnforcementAction(
            action=profile_violation_default,
            override_available=False,  # Just change profile for preferences
            reason=f"Preference constraint (profile={profile_violation_default})",
        )
