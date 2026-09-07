# SPDX-License-Identifier: Apache-2.0
"""Cross-process lock and frozen fingerprints for one fiction project."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def project_state_lock(context_root: Path) -> Iterator[None]:
    """Serialize canon/guidance mutation and generation acceptance.

    Lock order is always project state first, then a session lock. Canon and
    guidance writers never acquire a session lock.
    """
    directory = context_root / "marginalia"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = directory / ".project-state.lock"
    with lock_path.open("a+b") as handle:
        os.chmod(lock_path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def canon_fingerprint(context_root: Path) -> str:
    path = context_root / ".governor" / "continuity" / "anchors.json"
    content = path.read_bytes() if path.exists() else b""
    return _digest("marginalia.canon-fingerprint/v1", content)


def guidance_fingerprint(context_root: Path) -> str:
    path = context_root / "marginalia" / "project.json"
    if not path.exists():
        guidance = {
            "project_brief": "",
            "collaborator_stance": "",
            "voice_style_guidance": "",
        }
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        guidance = {
            "project_brief": value.get("project_brief", ""),
            "collaborator_stance": value.get("collaborator_stance", ""),
            "voice_style_guidance": value.get("voice_style_guidance", ""),
        }
    return _digest(
        "marginalia.guidance-fingerprint/v1",
        json.dumps(guidance, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(),
    )


def _digest(domain: str, content: bytes) -> str:
    return "sha256:" + hashlib.sha256(domain.encode("ascii") + b"\0" + content).hexdigest()
