# SPDX-License-Identifier: Apache-2.0
"""
Governor Context Manager: isolated governor contexts per user/project.

Each context gets its own .governor/ directory (and optional .fiction-gov/),
enabling multi-user or multi-project governor usage without cross-contamination.

Context directory layout:
    ~/.governor-contexts/
    +-- erin-novel-1/
    |   +-- _context.json          # GovernorContext metadata
    |   +-- .governor/             # Standard governor state
    |   +-- .fiction-gov/          # Fiction governor state (if mode=fiction)
    +-- james-agent-governor/
        +-- _context.json
        +-- .governor/
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Default base directory for all governor contexts
DEFAULT_BASE_DIR = Path.home() / ".governor-contexts"

# Valid context modes
VALID_MODES = {"general", "fiction", "code", "nonfiction", "research"}

# Context metadata filename
CONTEXT_META_FILE = "_context.json"

# Standard governor directory name
GOVERNOR_DIR = ".governor"

# Fiction governor directory name
FICTION_GOV_DIR = ".fiction-gov"

# Proposals file (v1 compat)
PROPOSALS_FILE = "proposals.json"


@dataclass
class GovernorContext:
    """An isolated governor context for a user/project."""

    context_id: str
    mode: str  # "fiction", "code", "nonfiction", "general"
    root: Path  # Context root directory
    governor_dir: Path  # root / ".governor"
    created_at: str  # ISO 8601 timestamp
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "context_id": self.context_id,
            "mode": self.mode,
            "root": str(self.root),
            "governor_dir": str(self.governor_dir),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GovernorContext:
        """Deserialize from dictionary."""
        return cls(
            context_id=data["context_id"],
            mode=data.get("mode", "general"),
            root=Path(data["root"]),
            governor_dir=Path(data["governor_dir"]),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )


def _validate_context_id(context_id: str) -> None:
    """Validate context ID format. Must be safe for filesystem use."""
    if not context_id:
        raise ValueError("Context ID cannot be empty")
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$", context_id):
        raise ValueError(
            f"Invalid context ID '{context_id}': must start with alphanumeric, "
            "contain only alphanumeric, dots, hyphens, underscores"
        )
    if len(context_id) > 128:
        raise ValueError(f"Context ID too long: {len(context_id)} chars (max 128)")


class GovernorContextManager:
    """Manages multiple isolated governor contexts on disk."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or DEFAULT_BASE_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        context_id: str,
        mode: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> GovernorContext:
        """Create a new governor context. Raises if already exists."""
        _validate_context_id(context_id)
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}': must be one of {VALID_MODES}")

        ctx_path = self._context_path(context_id)
        if ctx_path.exists():
            raise ValueError(f"Context '{context_id}' already exists")

        ctx_path.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()

        ctx = GovernorContext(
            context_id=context_id,
            mode=mode,
            root=ctx_path,
            governor_dir=ctx_path / GOVERNOR_DIR,
            created_at=now,
            metadata=metadata or {},
        )

        # Write metadata
        self._write_meta(ctx)

        # Initialize governor directory structure
        self._ensure_governor_init(ctx)

        return ctx

    def get(self, context_id: str) -> GovernorContext | None:
        """Get an existing context by ID. Returns None if not found."""
        meta_path = self._meta_path(context_id)
        if not meta_path.exists():
            return None
        return self._read_meta(meta_path)

    def get_or_create(
        self,
        context_id: str,
        mode: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> GovernorContext:
        """Get existing context or create a new one.

        Handles the case where the context directory exists but the
        metadata file is missing (e.g., from a previous partial init
        or session store creating the directory).
        """
        existing = self.get(context_id)
        if existing is not None:
            return existing

        ctx_path = self._context_path(context_id)
        if ctx_path.exists():
            # Directory exists but metadata is missing — repair it
            _validate_context_id(context_id)
            if mode not in VALID_MODES:
                raise ValueError(f"Invalid mode '{mode}': must be one of {VALID_MODES}")

            now = datetime.now(timezone.utc).isoformat()
            ctx = GovernorContext(
                context_id=context_id,
                mode=mode,
                root=ctx_path,
                governor_dir=ctx_path / GOVERNOR_DIR,
                created_at=now,
                metadata=metadata or {},
            )
            self._write_meta(ctx)
            self._ensure_governor_init(ctx)
            return ctx

        return self.create(context_id, mode=mode, metadata=metadata)

    def list_contexts(self) -> list[GovernorContext]:
        """List all contexts sorted by creation time."""
        contexts: list[GovernorContext] = []
        if not self.base_dir.exists():
            return contexts

        for entry in sorted(self.base_dir.iterdir()):
            if entry.is_dir():
                meta_path = entry / CONTEXT_META_FILE
                if meta_path.exists():
                    ctx = self._read_meta(meta_path)
                    if ctx is not None:
                        contexts.append(ctx)
        return contexts

    def delete(self, context_id: str) -> bool:
        """Delete a context and all its data. Returns True if deleted."""
        ctx_path = self._context_path(context_id)
        if not ctx_path.exists():
            return False

        # Recursively remove context directory
        import shutil

        shutil.rmtree(ctx_path)
        return True

    def _ensure_governor_init(self, ctx: GovernorContext) -> None:
        """Create .governor/ directory structure (mirrors `governor init`)."""
        gov_dir = ctx.governor_dir

        # Standard governor structure
        gov_dir.mkdir(exist_ok=True)
        (gov_dir / "facts").mkdir(exist_ok=True)
        (gov_dir / "facts" / "receipts").mkdir(exist_ok=True)
        (gov_dir / "decisions").mkdir(exist_ok=True)

        # Create empty index files (v1 compat) if they don't exist
        facts_index = gov_dir / "facts" / "index.json"
        if not facts_index.exists():
            facts_index.write_text("[]")

        decisions_index = gov_dir / "decisions" / "index.json"
        if not decisions_index.exists():
            decisions_index.write_text("[]")

        proposals_file = gov_dir / PROPOSALS_FILE
        if not proposals_file.exists():
            proposals_file.write_text("{}")

        # Fiction mode: also create .fiction-gov/
        if ctx.mode == "fiction":
            fiction_dir = ctx.root / FICTION_GOV_DIR
            fiction_dir.mkdir(exist_ok=True)

    def _context_path(self, context_id: str) -> Path:
        """Get filesystem path for a context."""
        return self.base_dir / context_id

    def _meta_path(self, context_id: str) -> Path:
        """Get path to context metadata file."""
        return self._context_path(context_id) / CONTEXT_META_FILE

    def _write_meta(self, ctx: GovernorContext) -> None:
        """Write context metadata to disk."""
        meta_path = ctx.root / CONTEXT_META_FILE
        meta_path.write_text(json.dumps(ctx.to_dict(), indent=2))

    def _read_meta(self, meta_path: Path) -> GovernorContext | None:
        """Read context metadata from disk."""
        try:
            data = json.loads(meta_path.read_text())
            context = GovernorContext.from_dict(data)
            # Stored absolute paths belonged to the classic layout. The live
            # location is established by the opened metadata descriptor.
            context.root = meta_path.parent
            context.governor_dir = meta_path.parent / GOVERNOR_DIR
            return context
        except (json.JSONDecodeError, KeyError):
            return None
