# SPDX-License-Identifier: Apache-2.0
"""Receipt v1 read-side store abstraction.

Defines ReceiptStore protocol and JsonlStore implementation.
The interface is designed so rotation, multi-file, and future SQLite
backends are implementation details — callers never care.

Primary API:
    iter_receipts(session_id, since, limit) → Iterator[Receipt]
    get_receipt(receipt_id) → Receipt | None
    verify_chain(session_id) → VerifyResult
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

from receipt_v1.types import Receipt
from receipt_v1.verify import VerifyResult, verify_chain as _verify_chain_dicts


class ReceiptStore(Protocol):
    """Read-side protocol for receipt storage.

    Implementations handle file layout, rotation, indexing.
    Callers get Receipt objects and never touch files.
    """

    def iter_receipts(
        self,
        *,
        session_id: str | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> Iterator[Receipt]:
        """Iterate receipts, newest first.

        Args:
            session_id: Filter by actor.session_id.
            since: ``timestamp_wall`` (ISO 8601 UTC, e.g. ``"2026-02-19T12:00:00Z"``).
                   Only receipts with ``timestamp_wall >= since`` are returned.
                   Lexicographic comparison — both sides must be UTC with ``Z`` suffix.
            limit: Maximum number of receipts to yield.
        """
        ...

    def get_receipt(self, receipt_id: str) -> Receipt | None:
        """Look up a single receipt by ID."""
        ...

    def verify_chain(self, *, session_id: str | None = None) -> VerifyResult:
        """Verify chain integrity across stored receipts.

        Args:
            session_id: If given, verify only that session's chain.
                       If None, verify all receipts as one chain.
        """
        ...


def _sorted_jsonl_files(base_path: Path) -> list[Path]:
    """Find base file + rotated files (.1, .2, ...), ordered newest-first.

    Rotation naming convention: file.jsonl, file.jsonl.1, file.jsonl.2, ...
    where .1 is newest rotated, .2 is next, etc.
    The base file (no suffix) is the current/newest.
    """
    files: list[Path] = []
    if base_path.exists():
        files.append(base_path)

    # Collect rotated files
    rotated: list[tuple[int, Path]] = []
    parent = base_path.parent
    name = base_path.name
    if parent.exists():
        for p in parent.iterdir():
            pname = p.name
            if pname.startswith(name + ".") and p.is_file():
                suffix = pname[len(name) + 1:]
                try:
                    n = int(suffix)
                    rotated.append((n, p))
                except ValueError:
                    pass  # Not a rotation suffix

    # Sort rotated by number (ascending = .1 newest, .2 older)
    rotated.sort(key=lambda t: t[0])
    files.extend(p for _, p in rotated)

    return files


def _read_jsonl_lines(path: Path) -> list[dict[str, Any]]:
    """Read all JSON lines from a single file."""
    lines: list[dict[str, Any]] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        pass  # Corrupt or missing — skip
    return lines


class JsonlStore:
    """ReceiptStore backed by JSONL files.

    Handles rotation transparently: reads the base file plus any
    rotated files (.1, .2, ...) produced by RotatingFileSink.

    Receipts are returned newest-first by default (reversed file order).
    Chain verification uses chronological order (oldest-first).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def _all_dicts_chronological(self) -> list[dict[str, Any]]:
        """Load all receipt dicts in chronological order (oldest first).

        Reads rotated files (oldest first), then the current file.
        Within each file, lines are in append order (oldest first).
        """
        files = _sorted_jsonl_files(self._path)
        # files is [current, .1, .2, ...] — reverse for chronological
        # .2 is oldest, .1 is middle, current is newest
        files.reverse()
        all_dicts: list[dict[str, Any]] = []
        for f in files:
            all_dicts.extend(_read_jsonl_lines(f))
        return all_dicts

    def iter_receipts(
        self,
        *,
        session_id: str | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> Iterator[Receipt]:
        """Iterate receipts, newest first.

        Args:
            session_id: Filter by actor.session_id.
            since: ``timestamp_wall`` (ISO 8601 UTC). Lexicographic >=.
            limit: Maximum number of receipts to yield.
        """
        dicts = self._all_dicts_chronological()
        # Newest first
        dicts.reverse()

        count = 0
        for d in dicts:
            # Filter by session_id
            if session_id is not None:
                actor = d.get("actor", {})
                if actor.get("session_id") != session_id:
                    continue

            # Filter by since (ISO 8601 string comparison works for UTC)
            if since is not None:
                ts = d.get("timestamp_wall", "")
                if ts < since:
                    continue

            # Limit
            if limit is not None and count >= limit:
                return

            try:
                yield Receipt.from_dict(d)
                count += 1
            except (KeyError, ValueError, TypeError):
                continue  # Skip malformed receipts

    def get_receipt(self, receipt_id: str) -> Receipt | None:
        """Look up a single receipt by ID across all files."""
        files = _sorted_jsonl_files(self._path)
        for f in files:
            for d in _read_jsonl_lines(f):
                if d.get("receipt_id") == receipt_id:
                    try:
                        return Receipt.from_dict(d)
                    except (KeyError, ValueError, TypeError):
                        return None
        return None

    def verify_chain(self, *, session_id: str | None = None) -> VerifyResult:
        """Verify chain integrity across all stored receipts.

        Uses chronological order. If session_id is given, verifies
        only that session's chain.
        """
        dicts = self._all_dicts_chronological()

        if session_id is not None:
            dicts = [
                d for d in dicts
                if d.get("actor", {}).get("session_id") == session_id
            ]

        return _verify_chain_dicts(dicts)
