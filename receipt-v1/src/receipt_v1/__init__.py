# SPDX-License-Identifier: Apache-2.0
"""Receipt v1: runtime-agnostic audit trail for tool governance.

Core API (stable, promised):
    ReceiptBuilder, ReceiptChain  — construct receipts
    FileSink, StdoutSink, Sink    — write receipts
    verify, verify_chain          — validate receipts
    Receipt, Action, Actor, ...   — types

Low-level (importable, cross-language contract, but not in __all__):
    receipt_v1.canonical.canonical_json   — Receipt Canonicalization v1
    receipt_v1.canonical.domain_hash      — domain-separated SHA-256
    receipt_v1.canonical.args_hash        — tool args hashing
    receipt_v1.canonical.result_hash      — tool result hashing
    receipt_v1.canonical.receipt_hash     — receipt content hashing
    receipt_v1.canonical.uuid7            — UUIDv7 generation

    These are the functions a TS emitter must reimplement. They are importable
    from receipt_v1.canonical and tested via tests/data/canonical_vectors.json.
    They are NOT in __all__ because most consumers only need ReceiptBuilder.
"""

__version__ = "0.1.0"

from receipt_v1.receipt import ReceiptBuilder, ReceiptChain
from receipt_v1.resources import schema_path
from receipt_v1.sinks import FileSink, Sink, StdoutSink
from receipt_v1.store import JsonlStore, ReceiptStore
from receipt_v1.types import (
    Action,
    Actor,
    AuthContext,
    Budget,
    Chain,
    Decision,
    EffectsConfidence,
    Execution,
    ExecutionStatus,
    Origin,
    PolicyOutcome,
    PolicyStep,
    Provenance,
    Receipt,
    Tool,
    Transport,
)
from receipt_v1.verify import VerifyResult, verify, verify_chain, verify_hash, verify_structure

__all__ = [
    # Builder — primary API
    "ReceiptBuilder", "ReceiptChain",
    # Types — enums + dataclasses
    "Action", "ExecutionStatus", "EffectsConfidence", "AuthContext", "Transport", "PolicyOutcome",
    "Actor", "Tool", "Origin", "Decision", "PolicyStep", "Budget", "Execution", "Chain",
    "Provenance", "Receipt",
    # Sinks
    "Sink", "FileSink", "StdoutSink",
    # Stores (read side)
    "ReceiptStore", "JsonlStore",
    # Verify
    "verify", "verify_structure", "verify_hash", "verify_chain", "VerifyResult",
    # Resources
    "schema_path",
]
