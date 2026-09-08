# SPDX-License-Identifier: Apache-2.0
"""Receipt v1 verification.

Structural validation, hash verification, chain integrity, secret detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from receipt_v1.canonical import _check_no_floats, receipt_hash as compute_receipt_hash
from receipt_v1.types import MAX_HUMAN_LEN, Action, Receipt

# ── Secret detection patterns ────────────────────────────────────────────────

SECRET_PATTERNS = [
    re.compile(r"(?:api[_-]?key|apikey)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"(?:secret|password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"(?:sk|pk|ak|rk)-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),
    re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----"),
    re.compile(r"(?:token|auth)\s*[:=]\s*[A-Za-z0-9\-._~+/]{20,}", re.IGNORECASE),
    re.compile(r"AWS[A-Z0-9]{10,}"),
]

# Hash pattern: exactly 64 hex chars
HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")

# Reason code: namespaced (e.g., "gov.scope_exceeded")
REASON_CODE_PATTERN = re.compile(r"^[a-z0-9]+\.[a-z0-9_]+$")

# Side effect pattern: category:name
SIDE_EFFECT_PATTERN = re.compile(r"^[a-z]+:[a-z0-9_]+$")

# Human-readable fields to check
HUMAN_FIELDS = [
    ("decision", "reason_human"),
    ("tool", "args_summary"),
    ("execution", "effect_summary"),
    ("execution", "result_summary"),
    ("execution", "error"),
]

# Receipt size soft ceiling
SIZE_SOFT_CEILING = 4096


@dataclass
class VerifyResult:
    """Result of receipt verification."""
    valid: bool
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return self.valid and not self.errors


def verify_structure(receipt_dict: dict[str, Any]) -> VerifyResult:
    """Verify structural validity of a receipt dict.

    Checks:
    - Required fields present
    - receipt_version = "1.0"
    - Hash format (64 hex chars)
    - Reason code format (namespaced)
    - Side effects sorted + unique
    - Human-readable field length caps
    - No secret patterns in human-readable fields
    - Action/status enum validity
    - Chain constraints (seq=1 no parent, seq>1 both parents)
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Required top-level fields
    for field in ("receipt_id", "receipt_version", "receipt_hash",
                  "chain", "timestamp_wall", "actor", "tool", "decision", "provenance"):
        if field not in receipt_dict:
            errors.append(f"Missing required field: {field}")

    if errors:
        return VerifyResult(valid=False, errors=errors, warnings=warnings)

    # Version
    if receipt_dict.get("receipt_version") != "1.0":
        v = receipt_dict.get("receipt_version", "")
        if isinstance(v, str) and v.startswith("1."):
            warnings.append(f"receipt_version {v} (expected 1.0, accepting 1.x)")
        else:
            errors.append(f"Unsupported receipt_version: {v}")

    # Hash format
    rh = receipt_dict.get("receipt_hash", "")
    if not HASH_PATTERN.match(rh):
        errors.append("receipt_hash is not a valid 64-char hex string")

    # Actor
    actor = receipt_dict.get("actor", {})
    for af in ("agent_id", "session_id"):
        if af not in actor:
            errors.append(f"Missing actor.{af}")

    # Tool
    tool = receipt_dict.get("tool", {})
    for tf in ("tool_id", "args_hash"):
        if tf not in tool:
            errors.append(f"Missing tool.{tf}")
    if "args_hash" in tool and not HASH_PATTERN.match(tool.get("args_hash", "")):
        errors.append("tool.args_hash is not a valid 64-char hex string")

    # Decision
    dec = receipt_dict.get("decision", {})
    if "action" not in dec:
        errors.append("Missing decision.action")
    elif dec["action"] not in ("allow", "deny", "transform", "escalate"):
        errors.append(f"Invalid decision.action: {dec['action']}")

    if "reason_code" not in dec:
        errors.append("Missing decision.reason_code")
    elif not REASON_CODE_PATTERN.match(dec.get("reason_code", "")):
        errors.append(f"Invalid reason_code format: {dec['reason_code']}")

    # transformed_args_hash only with transform
    if dec.get("transformed_args_hash") and dec.get("action") != "transform":
        warnings.append("transformed_args_hash present but action is not 'transform'")

    # Chain
    chain = receipt_dict.get("chain", {})
    if "seq" not in chain:
        errors.append("Missing chain.seq")
    else:
        seq = chain["seq"]
        if not isinstance(seq, int) or seq < 1:
            errors.append("chain.seq must be a positive integer")
        elif seq == 1:
            if "parent_receipt_id" in chain or "parent_receipt_hash" in chain:
                errors.append("chain: seq=1 must not have parent fields")
        elif seq > 1:
            if "parent_receipt_id" not in chain or "parent_receipt_hash" not in chain:
                errors.append("chain: seq>1 requires both parent_receipt_id and parent_receipt_hash")

    # Provenance
    prov = receipt_dict.get("provenance", {})
    for pf in ("deployment_id", "instance_id", "governor_version"):
        if pf not in prov:
            errors.append(f"Missing provenance.{pf}")

    # Execution (only for allow/transform)
    if "execution" in receipt_dict:
        ex = receipt_dict["execution"]
        if "status" not in ex:
            errors.append("Missing execution.status")
        elif ex["status"] not in ("success", "failure", "timeout", "skipped"):
            errors.append(f"Invalid execution.status: {ex['status']}")
        if ex.get("attempt", 1) < 1:
            errors.append("execution.attempt must be >= 1")
        if ex.get("duration_ms") is not None and ex["duration_ms"] < 0:
            errors.append("execution.duration_ms must be >= 0")

        # Side effects: sorted + unique
        effects = ex.get("side_effects")
        if effects is not None:
            if effects != sorted(set(effects)):
                errors.append("side_effects must be sorted and unique")
            for eff in effects:
                if not SIDE_EFFECT_PATTERN.match(eff):
                    errors.append(f"Invalid side_effect format: {eff}")

    # Human-readable field checks: length + secret scan
    for section, field in HUMAN_FIELDS:
        container = receipt_dict.get(section, {})
        if isinstance(container, dict) and field in container:
            val = container[field]
            if isinstance(val, str):
                if len(val) > MAX_HUMAN_LEN:
                    errors.append(f"{section}.{field} exceeds {MAX_HUMAN_LEN} chars")
                if "\n" in val or "\r" in val:
                    errors.append(f"{section}.{field} contains newlines")
                for pat in SECRET_PATTERNS:
                    if pat.search(val):
                        errors.append(f"Possible secret in {section}.{field}")
                        break

    # Empty string check for optional fields
    for opt_field in ("policy_id", "policy_version"):
        if dec.get(opt_field) == "":
            errors.append(f"decision.{opt_field} must not be empty string; omit instead")

    for opt_field in ("tool_version", "call_id", "capability_asserted"):
        if tool.get(opt_field) == "":
            errors.append(f"tool.{opt_field} must not be empty string; omit instead")

    # ext validation: namespaced keys, no floats
    ext = receipt_dict.get("ext")
    if ext is not None:
        if not isinstance(ext, dict):
            errors.append("ext must be an object")
        elif not ext:
            errors.append("ext must not be empty (omit instead)")
        else:
            ext_key_pattern = re.compile(r"^[a-z0-9]+\.[a-z0-9_.]+$")
            for key in ext:
                if not ext_key_pattern.match(key):
                    errors.append(f"ext key {key!r} must be namespaced (e.g. 'vendor.field')")
            try:
                _check_no_floats(ext, "ext")
            except ValueError as e:
                errors.append(str(e))

    # Size warning
    import json
    size = len(json.dumps(receipt_dict).encode("utf-8"))
    if size > SIZE_SOFT_CEILING:
        warnings.append(f"Receipt size {size} bytes exceeds soft ceiling of {SIZE_SOFT_CEILING}")

    return VerifyResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


def verify_hash(receipt_dict: dict[str, Any]) -> VerifyResult:
    """Verify that receipt_hash matches recomputed hash."""
    errors: list[str] = []
    warnings: list[str] = []

    stored = receipt_dict.get("receipt_hash", "")
    recomputed = compute_receipt_hash(receipt_dict)

    if stored != recomputed:
        errors.append(f"Hash mismatch: stored={stored}, computed={recomputed}")

    return VerifyResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


def verify_chain(receipts: list[dict[str, Any]]) -> VerifyResult:
    """Verify chain integrity across a sequence of receipt dicts.

    Checks:
    - Sequential seq numbers
    - parent_receipt_id matches previous receipt_id
    - parent_receipt_hash matches previous receipt_hash
    - Individual hash verification for each receipt
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not receipts:
        return VerifyResult(valid=True, errors=errors, warnings=warnings)

    prev: dict[str, Any] | None = None

    for i, r in enumerate(receipts):
        # Verify individual hash
        hr = verify_hash(r)
        errors.extend(hr.errors)

        chain = r.get("chain", {})
        seq = chain.get("seq", 0)

        if prev is not None:
            prev_chain = prev.get("chain", {})
            prev_seq = prev_chain.get("seq", 0)

            # Check seq continuity
            if seq != prev_seq + 1:
                if seq > prev_seq + 1:
                    warnings.append(f"Seq gap: {prev_seq} -> {seq} (missing {seq - prev_seq - 1} receipts)")
                else:
                    errors.append(f"Seq not monotonic at index {i}: {prev_seq} -> {seq}")

            # Check parent pointers
            if "parent_receipt_id" in chain:
                if chain["parent_receipt_id"] != prev.get("receipt_id"):
                    errors.append(
                        f"Receipt seq={seq}: parent_receipt_id mismatch "
                        f"(expected {prev.get('receipt_id')}, got {chain['parent_receipt_id']})"
                    )
            if "parent_receipt_hash" in chain:
                if chain["parent_receipt_hash"] != prev.get("receipt_hash"):
                    errors.append(
                        f"Receipt seq={seq}: parent_receipt_hash mismatch "
                        f"(expected {prev.get('receipt_hash')}, got {chain['parent_receipt_hash']})"
                    )

        prev = r

    return VerifyResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


def verify(receipt_dict: dict[str, Any]) -> VerifyResult:
    """Full verification: structure + hash."""
    sr = verify_structure(receipt_dict)
    if not sr.valid:
        return sr

    hr = verify_hash(receipt_dict)
    return VerifyResult(
        valid=sr.valid and hr.valid,
        errors=sr.errors + hr.errors,
        warnings=sr.warnings + hr.warnings,
    )
