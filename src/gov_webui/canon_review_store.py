# SPDX-License-Identifier: Apache-2.0
"""Durable, project-scoped review queue for possible canon.

Generated suggestions are exploratory records. Only an explicit acceptance
action may promote one into the canonical continuity registry.

Every candidate carries a **warrant**: what makes it a change to the author's
canon rather than an opinion about it. The distinction is load-bearing. A
statement lifted from the author's own prose, or a stored record that provably
disagrees with the source it was derived from, is something the product can
offer to write. A reading of what the author's words imply is not, however
confident or well argued — the author's material means what the author says it
means, and an analysis pass that disagrees has found a question, not a defect.

So warrants divide in two, and only source-fidelity warrants may carry a
proposed mutation. Interpretive ones survive as diagnostics the author can read
and dismiss, and dismissal is sticky: a later pass that re-derives the same
claim from the same evidence must not reopen a question the author has closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CanonReviewStoreError(RuntimeError):
    """The canon review queue could not be read or persisted."""


class CanonReviewNotFoundError(CanonReviewStoreError):
    """A requested review candidate does not exist."""


class CanonResolutionStandsError(CanonReviewStoreError):
    """The author already resolved this claim on this evidence."""


# Warrants that can be shown true against the source, without preferring one
# reading of the author's words over another.
MUTATION_ADMISSIBLE_WARRANTS: frozenset[str] = frozenset(
    {
        # The author wrote it; capture only relocates it into canon.
        "author_statement",
        # Stored canon has provably lost or altered part of its source.
        "source_omission",
        "source_truncation",
        "dropped_subject",
        "dangling_reference",
        "transformation_mismatch",
        # Stored canon is filed as the wrong kind of thing, or disagrees with
        # itself. Neither judgement needs the text to be interpreted.
        "category_misclassification",
        "duplicate_state",
        "contradictory_state",
        # A narrowing the author licensed by saying the category is complete.
        # Admissible only alongside a `category` the registry actually closes,
        # which promotion checks; the warrant alone proves nothing.
        "established_closure",
    }
)

# Warrants that exist only in a reading. Useful to surface; never a licence to
# edit. `strengthened_proposition` is the specific failure this boundary was
# built for: source states an exclusion or a necessary condition, an analysis
# pass reads sufficiency into it, then offers the author's own material as a
# counterexample to a claim the author never made.
DIAGNOSTIC_ONLY_WARRANTS: frozenset[str] = frozenset(
    {
        "inferred_implication",
        "strengthened_proposition",
        "scope_interpretation",
        "proposed_formalization",
        "ontology_clarification",
        "interpretive_ambiguity",
    }
)

KNOWN_WARRANTS: frozenset[str] = MUTATION_ADMISSIBLE_WARRANTS | DIAGNOSTIC_ONLY_WARRANTS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def evidence_fingerprint(*, kind: str, subject: str, statement: str, target: str | None) -> str:
    """Identify a claim by its evidence, not by the argument made from it.

    The warrant is deliberately excluded. If a later pass reaches the same claim
    about the same subject by a different route, it is still the claim the
    author already answered, and it stays closed.
    """
    payload = "\u241f".join(
        [
            kind.strip().casefold(),
            " ".join(subject.split()).casefold(),
            " ".join(statement.split()).casefold(),
            (target or "").strip().casefold(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CanonReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    kind: str
    confidence: float = 0.0
    subject: str = ""
    statement: str
    field: str = ""
    spans: list[list[int]] = Field(default_factory=list)
    conversation_id: str | None = None
    message_id: str = ""
    status: Literal["pending", "accepted", "dismissed"] = "pending"
    draft: dict[str, Any] | None = None
    promoted_to: str | None = None
    # What makes this a change to canon rather than an opinion about it.
    # Records written before warrants existed came from prose capture, which is
    # exactly `author_statement`, so the default reads them correctly.
    warrant: str = "author_statement"
    # The anchor a repair proposal would rewrite. Absent for ordinary capture,
    # which adds canon rather than altering it.
    target_anchor_id: str | None = None
    # Propositions this candidate depends on. Each must be traceable to the
    # author's canon or source; one that is not means the candidate rests on
    # something the model supplied, and it cannot authorise an edit.
    relied_on: list[str] = Field(default_factory=list)
    # Set when the argument depends on knowing a category's full extent. The
    # observed cast never establishes that, so promotion requires the author to
    # have closed the category in canon. Empty means the candidate makes no
    # claim about how many members exist, which is the ordinary case.
    category: str = ""
    created_at: str
    updated_at: str

    @property
    def mutation_admissible(self) -> bool:
        """True when this candidate may carry a proposed edit to canon."""
        return self.warrant in MUTATION_ADMISSIBLE_WARRANTS

    def fingerprint(self) -> str:
        return evidence_fingerprint(
            kind=self.kind,
            subject=self.subject,
            statement=self.statement,
            target=self.target_anchor_id,
        )


class CanonReviewState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    next_sequence: int = 1
    items: dict[str, CanonReviewItem] = Field(default_factory=dict)
    updated_at: str


class CanonAuthorityState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    anchor_sequences: dict[str, int] = Field(default_factory=dict)
    explicit_closures: dict[str, str] = Field(default_factory=dict)


class CanonReviewStore:
    """Atomic file-backed canon candidate queue for one project."""

    def __init__(self, path: Path, *, project_id: str) -> None:
        self.path = path
        self.project_id = project_id
        self._lock = threading.RLock()
        self._state = self._load_or_create()
        self.authority_path = path.with_name("canon-authority.json")
        self._authority = self._load_authority()

    def _load_authority(self) -> CanonAuthorityState:
        if not self.authority_path.exists():
            return CanonAuthorityState()
        try:
            return CanonAuthorityState.model_validate_json(
                self.authority_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            raise CanonReviewStoreError(
                f"cannot load canon authority metadata at {self.authority_path}: {exc}"
            ) from exc

    def _write_authority(self) -> None:
        temporary = self.authority_path.with_suffix(".json.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(self._authority.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.authority_path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise CanonReviewStoreError(
                f"cannot persist canon authority metadata at {self.authority_path}: {exc}"
            ) from exc

    def _fresh(self) -> CanonReviewState:
        return CanonReviewState(updated_at=_now())

    def _load_or_create(self) -> CanonReviewState:
        if not self.path.exists():
            state = self._fresh()
            self._write(state)
            return state
        try:
            return CanonReviewState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            raise CanonReviewStoreError(
                f"cannot load canon review queue at {self.path}: {exc}"
            ) from exc

    def _write(self, state: CanonReviewState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(state.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise CanonReviewStoreError(
                f"cannot persist canon review queue at {self.path}: {exc}"
            ) from exc

    def _save(self) -> None:
        self._state.updated_at = _now()
        self._write(self._state)

    def allocate_anchor_id(self, prefix: str, existing_ids: set[str]) -> str:
        """Reserve a monotonically increasing id before canon is written."""
        with self._lock:
            numeric = [
                int(value[len(prefix) + 1 :])
                for value in existing_ids
                if value.startswith(f"{prefix}-") and value[len(prefix) + 1 :].isdigit()
            ]
            index = max(
                self._authority.anchor_sequences.get(prefix, 1), max(numeric, default=0) + 1
            )
            while f"{prefix}-{index}" in existing_ids:
                index += 1
            self._authority.anchor_sequences[prefix] = index + 1
            self._write_authority()
            return f"{prefix}-{index}"

    def record_explicit_closure(self, anchor_id: str, category: str) -> None:
        """Record an author-confirmed category closure, never an inferred one."""
        cleaned = " ".join(category.split())
        if not cleaned:
            raise ValueError("closed category must not be empty")
        with self._lock:
            self._authority.explicit_closures[anchor_id] = cleaned
            self._write_authority()

    def explicit_closure_anchor_ids(self, category: str) -> list[str]:
        target = " ".join(category.split()).casefold()
        with self._lock:
            return [
                anchor_id
                for anchor_id, closed in self._authority.explicit_closures.items()
                if closed.casefold() == target
            ]

    def resolved_fingerprints(self) -> dict[str, str]:
        """Evidence the author has already answered, mapped to the answer."""
        with self._lock:
            return {
                item.fingerprint(): item.status
                for item in self._state.items.values()
                if item.status != "pending"
            }

    def add(
        self,
        *,
        kind: str,
        statement: str,
        confidence: float = 0.0,
        subject: str = "",
        field: str = "",
        spans: list[list[int]] | None = None,
        conversation_id: str | None = None,
        message_id: str = "",
        draft: dict[str, Any] | None = None,
        warrant: str = "author_statement",
        target_anchor_id: str | None = None,
        relied_on: list[str] | None = None,
        category: str = "",
    ) -> CanonReviewItem:
        if not statement.strip():
            raise ValueError("canon review statement must not be empty")
        if warrant not in KNOWN_WARRANTS:
            raise ValueError(f"unknown canon review warrant: {warrant!r}")
        if target_anchor_id and warrant == "author_statement":
            # Rewriting an existing anchor is a claim about stored state, and
            # needs a warrant that says what is wrong with it.
            raise ValueError(
                "a proposal targeting an existing anchor needs a source-fidelity warrant"
            )
        with self._lock:
            fingerprint = evidence_fingerprint(
                kind=kind, subject=subject, statement=statement, target=target_anchor_id
            )
            standing = self.resolved_fingerprints().get(fingerprint)
            if standing is not None:
                # The author has answered this claim on this evidence. Deriving
                # it again is not new information, so it does not reopen.
                raise CanonResolutionStandsError(
                    f"the author already {standing} this claim; "
                    "reopening it requires new authoritative evidence"
                )
            candidate_id = f"cap-{self._state.next_sequence}"
            self._state.next_sequence += 1
            now = _now()
            item = CanonReviewItem(
                id=candidate_id,
                project_id=self.project_id,
                kind=kind,
                confidence=confidence,
                subject=subject,
                statement=statement,
                field=field,
                spans=spans or [],
                conversation_id=conversation_id,
                message_id=message_id,
                draft=draft,
                warrant=warrant,
                target_anchor_id=target_anchor_id,
                relied_on=list(relied_on or []),
                category=category.strip(),
                created_at=now,
                updated_at=now,
            )
            self._state.items[item.id] = item
            self._save()
            return item.model_copy(deep=True)

    def get(self, candidate_id: str) -> CanonReviewItem:
        with self._lock:
            item = self._state.items.get(candidate_id)
            if item is None:
                raise CanonReviewNotFoundError(f"canon review candidate not found: {candidate_id}")
            return item.model_copy(deep=True)

    def list(
        self,
        *,
        status: Literal["pending", "accepted", "dismissed", "all"] = "pending",
    ) -> list[CanonReviewItem]:
        with self._lock:
            items = [
                item.model_copy(deep=True)
                for item in self._state.items.values()
                if status == "all" or item.status == status
            ]
        items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return items

    def resolve(
        self,
        candidate_id: str,
        *,
        status: Literal["accepted", "dismissed"],
        promoted_to: str | None = None,
    ) -> CanonReviewItem:
        with self._lock:
            item = self._state.items.get(candidate_id)
            if item is None:
                raise CanonReviewNotFoundError(f"canon review candidate not found: {candidate_id}")
            if item.status != "pending":
                raise ValueError(f"canon review candidate already {item.status}")
            item.status = status
            item.promoted_to = promoted_to
            item.updated_at = _now()
            self._save()
            return item.model_copy(deep=True)

    def update(
        self,
        candidate_id: str,
        *,
        subject: str | None = None,
        statement: str | None = None,
        kind: str | None = None,
    ) -> CanonReviewItem:
        """Edit a pending suggestion before an explicit canon decision."""
        with self._lock:
            item = self._state.items.get(candidate_id)
            if item is None:
                raise CanonReviewNotFoundError(f"canon review candidate not found: {candidate_id}")
            if item.status != "pending":
                raise ValueError(f"canon review candidate already {item.status}")
            if subject is not None:
                item.subject = " ".join(subject.split())
            if statement is not None:
                cleaned = statement.strip()
                if not cleaned:
                    raise ValueError("canon review statement must not be empty")
                item.statement = cleaned
            if kind is not None:
                cleaned_kind = kind.strip()
                if not cleaned_kind:
                    raise ValueError("canon review kind must not be empty")
                item.kind = cleaned_kind
            item.updated_at = _now()
            self._save()
            return item.model_copy(deep=True)
