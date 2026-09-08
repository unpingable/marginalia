# SPDX-License-Identifier: Apache-2.0
"""Receipt Canonicalization v1.

This is the authoritative specification for Receipt v1 canonical form.
Any emitter (Python, TypeScript, Rust, etc.) MUST produce byte-identical
output for the same input. The test vectors in tests/data/canonical_vectors.json
are the cross-language contract.

Rules (all mandatory, no exceptions):

  1. Sorted keys at every nesting level (lexicographic, Unicode code point order).
  2. Compact separators: "," between elements, ":" between key and value.
     No whitespace anywhere.
  3. ensure_ascii=True: all non-ASCII characters encoded as \\uXXXX escapes.
     This includes multi-byte Unicode (e.g. U+2603 -> \\u2603).
  4. allow_nan=False: NaN, Infinity, -Infinity are forbidden.
  5. No floats anywhere in hashed structures. All numeric values must be int.
     This prevents cross-platform rounding divergence.
  6. No empty strings for optional fields. Omit the field entirely.
  7. UTF-8 encoding of the resulting JSON string -> bytes for hashing.

Forbidden (will raise ValueError):
  - Float values (3.14, 1.0, float("inf"), float("nan"))
  - NaN / Infinity (via allow_nan=False)

Not forbidden but handled:
  - Null (None) -> JSON null (included in canonical form)
  - Empty arrays [] -> included
  - Empty objects {} -> included (but empty ext is omitted by the builder)
  - Negative integers -> included as-is
  - Strings with special chars -> JSON-escaped (\\t, \\n, \\\\, \\")

Domain-separated SHA-256 hashing:
  H(domain, part1, part2, ...) = SHA-256(domain + \\x00 + part1 + \\x00 + part2 + ...)
  Returns lowercase hex (64 chars for SHA-256).

UUIDv7 (RFC 9562): time-sortable + random. Version nibble = 7, variant = 10.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import time
from typing import Any


def _check_no_floats(obj: Any, path: str = "") -> None:
    """Raise ValueError if any float values exist in the structure."""
    if isinstance(obj, float):
        raise ValueError(f"Float values are forbidden in hashed structures (at {path or 'root'})")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _check_no_floats(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check_no_floats(v, f"{path}[{i}]")


def canonical_json(obj: Any, *, check_floats: bool = True) -> bytes:
    """Produce canonical JSON bytes for hashing.

    Rules (Receipt Canonicalization v1):
    - Sorted keys at every nesting level
    - Compact separators
    - ensure_ascii=True
    - allow_nan=False
    - No floats allowed (enforced by default)
    - Returns UTF-8 encoded bytes
    """
    if check_floats:
        _check_no_floats(obj)
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True, allow_nan=False)
    return s.encode("utf-8")


def domain_hash(domain: str, *parts: bytes) -> str:
    """Domain-separated SHA-256 hash.

    H(domain, part1, part2, ...) = SHA-256(domain.encode("utf-8") + b"\\x00" + part1 + b"\\x00" + part2 + ...)

    Returns lowercase hex string (64 chars for SHA-256).
    """
    h = hashlib.sha256()
    h.update(domain.encode("utf-8"))
    for part in parts:
        h.update(b"\x00")
        h.update(part)
    return h.hexdigest()


def args_hash(tool_id: str, args: Any) -> str:
    """Hash tool arguments with domain separation.

    H("args-v1", tool_id_bytes, canonical_json(args))
    """
    return domain_hash("args-v1", tool_id.encode("utf-8"), canonical_json(args))


def result_hash(tool_id: str, sanitized_result: Any) -> str:
    """Hash sanitized tool result with domain separation.

    H("result-v1", tool_id_bytes, canonical_json(sanitized_result))
    """
    return domain_hash("result-v1", tool_id.encode("utf-8"), canonical_json(sanitized_result))


def receipt_hash(receipt_dict: dict[str, Any]) -> str:
    """Compute receipt hash, excluding self-referential and signature fields.

    H("receipt-v1", canonical_json(receipt_minus_excluded))

    Excluded fields:
    - receipt_hash (self-referential)
    - provenance.signature, .signature_alg, .signature_encoding, .signing_key_id
    """
    excluded_top = {"receipt_hash"}
    excluded_provenance = {"signature", "signature_alg", "signature_encoding", "signing_key_id"}

    filtered = {}
    for k, v in receipt_dict.items():
        if k in excluded_top:
            continue
        if k == "provenance" and isinstance(v, dict):
            filtered[k] = {pk: pv for pk, pv in v.items() if pk not in excluded_provenance}
        else:
            filtered[k] = v

    return domain_hash("receipt-v1", canonical_json(filtered))


def uuid7() -> str:
    """Generate a UUIDv7 (RFC 9562).

    48-bit millisecond timestamp + 4-bit version (7) + 12-bit random +
    2-bit variant (10) + 62-bit random.

    Returns lowercase string with hyphens (standard UUID format).
    """
    ms = int(time.time() * 1000)
    rand_bytes = os.urandom(10)

    # 48-bit timestamp (big-endian)
    ts_bytes = struct.pack(">Q", ms)[2:]  # last 6 bytes of 8-byte big-endian

    # Build 16 bytes
    b = bytearray(16)
    b[0:6] = ts_bytes
    b[6:16] = rand_bytes

    # Set version = 7 (bits 48-51)
    b[6] = (b[6] & 0x0F) | 0x70

    # Set variant = 10 (bits 64-65)
    b[8] = (b[8] & 0x3F) | 0x80

    hex_str = b.hex()
    return f"{hex_str[0:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:32]}"
