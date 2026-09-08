# SPDX-License-Identifier: Apache-2.0
"""Receipt sinks: where receipts go after creation.

Sink protocol + two built-in sinks:
- FileSink: append to JSONL file (with optional fsync for durability)
- StdoutSink: write to stdout (for piping / debugging)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

from receipt_v1.types import Receipt


class Sink(Protocol):
    """Protocol for receipt sinks."""

    def write(self, receipt: Receipt) -> None:
        """Write a receipt to the sink."""
        ...


class FileSink:
    """Append receipts as JSONL to a file.

    Args:
        path: File path for receipt storage.
        fsync: If True (default), flush + fsync per receipt for durability.
               Set to False for dev/testing (faster, less durable).
    """

    def __init__(self, path: str | Path, *, fsync: bool = True) -> None:
        self._path = Path(path)
        self._fsync = fsync

    @property
    def path(self) -> Path:
        return self._path

    def write(self, receipt: Receipt) -> None:
        """Append receipt as a single JSON line."""
        line = json.dumps(receipt.to_dict(), separators=(",", ":"), sort_keys=True) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            if self._fsync:
                os.fsync(fd)
        finally:
            os.close(fd)

    def read_all(self) -> list[dict[str, Any]]:
        """Read all receipts from the file. Returns list of dicts."""
        if not self._path.exists():
            return []
        receipts = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    receipts.append(json.loads(line))
        return receipts


class StdoutSink:
    """Write receipts to stdout as JSON lines."""

    def write(self, receipt: Receipt) -> None:
        """Write receipt as a single JSON line to stdout."""
        line = json.dumps(receipt.to_dict(), separators=(",", ":"), sort_keys=True)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
