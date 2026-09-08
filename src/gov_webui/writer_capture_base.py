# SPDX-License-Identifier: Apache-2.0
"""
Generic capture protocol: detect structured intent in chat and stage for promotion.

Draft surface (chat) → Capture classifier → Pending objects → Explicit promotion.

This module provides the base types, helpers, and ABC that domain-specific
capture classifiers (fiction, code, research) implement. The key invariant:
**no auto-promote, ever.** All captures start PENDING and require explicit
user action to become canonical.

Domain classifiers:
- Fiction (canon_capture): characters, world rules, relationships, constraints
- Code (this module): work items, hypotheses, decisions, patch intents
- Research (this module): claims, citations, experiments, assumptions

Span semantics:
  Spans are character offsets into the EXACT string passed to scan().
  The content_hash in the receipt is SHA-256 of that same string.
  No normalization, no trimming. If you pre-process, do it before scan().
"""

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# =============================================================================
# Generic capture types
# =============================================================================


class CaptureStatus(str, Enum):
    """Lifecycle status of a captured item."""

    PENDING = "pending"
    """Detected but not yet acted on by the user."""

    ACCEPTED = "accepted"
    """User promoted to canonical store."""

    REJECTED = "rejected"
    """User dismissed this capture."""

    EXPIRED = "expired"
    """Session ended without user action."""


@dataclass
class CapturedItem:
    """A provisional structured object detected in chat.

    Domain-agnostic. The ``kind`` field is a string whose valid values
    are defined by each domain classifier (e.g., "character", "work_item").
    """

    kind: str
    """Domain-specific kind (e.g., 'character', 'work_item', 'claim')."""

    confidence: float
    """0.0–1.0. Higher = more likely a real definition."""

    subject_guess: str | None
    """Extracted key entity (character name, module path, etc.)."""

    statement: str
    """The raw text that triggered detection."""

    field_guess: str
    """Suggested field in the canonical store (e.g., 'description', 'task')."""

    evidence_spans: list[tuple[int, int]]
    """Character offset spans in the EXACT input string (same as hashed)."""

    source_message_id: str = ""
    """Chat message ID for provenance."""

    status: CaptureStatus = CaptureStatus.PENDING
    """Always starts PENDING. Promotion is explicit."""

    draft_payload: dict[str, str] = field(default_factory=dict)
    """Pre-filled data for the promotion UI."""

    subject_source: str = ""
    """Which pattern produced the subject_guess (for dedup diagnostics)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "confidence": self.confidence,
            "subject_guess": self.subject_guess,
            "statement": self.statement,
            "field_guess": self.field_guess,
            "evidence_spans": [list(s) for s in self.evidence_spans],
            "source_message_id": self.source_message_id,
            "status": self.status.value,
            "draft_payload": self.draft_payload,
            "subject_source": self.subject_source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapturedItem":
        return cls(
            kind=data["kind"],
            confidence=data["confidence"],
            subject_guess=data.get("subject_guess"),
            statement=data["statement"],
            field_guess=data["field_guess"],
            evidence_spans=[tuple(s) for s in data.get("evidence_spans", [])],
            source_message_id=data.get("source_message_id", ""),
            status=CaptureStatus(data.get("status", "pending")),
            draft_payload=data.get("draft_payload", {}),
            subject_source=data.get("subject_source", ""),
        )


@dataclass
class CaptureReceipt:
    """Determinism receipt for a capture run.

    Same input text always produces same content_hash and pattern_hits
    (timestamp varies). Proves what patterns fired, what spans matched,
    and which classifier version ran.
    """

    classifier_version: str
    """Format: '{domain}@{semver}', e.g. 'fiction.canon@1.0.0'."""

    content_hash: str
    """SHA-256 of the EXACT input string (no normalization)."""

    pattern_hits: list[dict[str, Any]]
    """[{pattern_name, category, span, matched_text}]."""

    items_detected: int
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "classifier_version": self.classifier_version,
            "content_hash": self.content_hash,
            "pattern_hits": self.pattern_hits,
            "items_detected": self.items_detected,
            "timestamp": self.timestamp,
        }


# =============================================================================
# Capture classifier ABC
# =============================================================================


class CaptureClassifier(ABC):
    """Base class for domain-specific capture classifiers.

    Subclasses implement ``_get_patterns()`` and ``_get_version()``.
    The scan loop, filtering, dedup, and receipt emission are handled
    by the base class.
    """

    def __init__(self, max_captures: int = 5):
        self.max_captures = max_captures

    @abstractmethod
    def _get_version(self) -> str:
        """Return forensic version string, e.g. 'fiction.canon@1.0.0'."""
        ...

    @abstractmethod
    def _get_patterns(self) -> list["PatternDef"]:
        """Return compiled pattern definitions for this domain."""
        ...

    def scan(
        self,
        text: str,
        message_id: str = "",
    ) -> tuple[list[CapturedItem], CaptureReceipt]:
        """Scan a chat message for structured intent.

        Args:
            text: The chat message text (spans index into this exact string).
            message_id: Chat message ID for provenance.

        Returns:
            Tuple of (captures, receipt). Captures are always PENDING.
        """
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        pattern_hits: list[dict[str, Any]] = []
        items: list[CapturedItem] = []

        for line in split_into_statements(text):
            line_text = line["text"]
            line_offset = line["offset"]

            skip_reason = classify_skippable(line_text)

            # Hard skip for hypotheticals and meta-discussion
            if skip_reason in ("hypothetical", "meta"):
                continue

            # Question-tagged assertions get reduced confidence
            confidence_multiplier = 1.0
            if skip_reason == "question":
                tag_stripped = strip_question_tag(line_text)
                if tag_stripped and tag_stripped != line_text:
                    # It's a question-tagged assertion — proceed at half confidence
                    line_text = tag_stripped
                    confidence_multiplier = 0.5
                else:
                    # Pure question — skip
                    continue

            for pdef in self._get_patterns():
                for match in pdef.regex.finditer(line_text):
                    span_start = line_offset + match.start()
                    span_end = line_offset + match.end()
                    matched_text = match.group(0).strip()

                    subject = pdef.extract_subject(match)
                    if subject and subject.lower() in CAPTURE_STOPWORDS:
                        subject = None

                    pattern_hits.append(
                        {
                            "pattern_name": pdef.name,
                            "category": pdef.category,
                            "span": [span_start, span_end],
                            "matched_text": matched_text[:200],
                        }
                    )

                    statement = pdef.extract_statement(match)
                    draft = pdef.build_draft(subject, statement)
                    effective_confidence = pdef.confidence * confidence_multiplier

                    item = CapturedItem(
                        kind=pdef.kind,
                        confidence=effective_confidence,
                        subject_guess=subject,
                        statement=statement,
                        field_guess=pdef.field_guess,
                        evidence_spans=[(span_start, span_end)],
                        source_message_id=message_id,
                        status=CaptureStatus.PENDING,
                        draft_payload=draft,
                        subject_source=pdef.name,
                    )
                    items.append(item)

        items = dedup_captures(items)
        items.sort(key=lambda c: c.confidence, reverse=True)
        items = items[: self.max_captures]

        receipt = CaptureReceipt(
            classifier_version=self._get_version(),
            content_hash=content_hash,
            pattern_hits=pattern_hits,
            items_detected=len(items),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        return items, receipt


# =============================================================================
# Pattern definition
# =============================================================================


@dataclass
class PatternDef:
    """A single detection pattern with extraction helpers."""

    name: str
    category: str
    regex: re.Pattern
    kind: str
    """Domain-specific kind string (e.g., 'character', 'work_item')."""

    confidence: float
    field_guess: str

    subject_group: int | None = 0
    """Regex group index for the subject. None = no subject."""

    statement_group: int | None = None
    """Regex group index for the statement. None = full match."""

    draft_fields: dict[str, str] = field(default_factory=dict)
    """Template for draft payload. Keys with '$subject' or '$statement' are replaced."""

    def extract_subject(self, match: re.Match) -> str | None:
        """Extract the subject entity from a regex match."""
        if self.subject_group is None:
            return None
        groups = match.groups()
        if self.subject_group < len(groups):
            name = groups[self.subject_group]
            if name and len(name) >= 2:
                if name[0].isupper() and name.lower() not in CAPTURE_STOPWORDS:
                    return name.strip()
        return extract_proper_noun(match.group(0))

    def extract_statement(self, match: re.Match) -> str:
        """Extract the statement text from a match."""
        if self.statement_group is not None:
            groups = match.groups()
            if self.statement_group < len(groups) and groups[self.statement_group]:
                return groups[self.statement_group].strip()
        return match.group(0).strip()

    def build_draft(self, subject: str | None, statement: str) -> dict[str, str]:
        """Build pre-filled payload for the promotion UI."""
        draft: dict[str, str] = {}
        for key, template in self.draft_fields.items():
            val = template
            if "$subject" in val and subject:
                val = val.replace("$subject", subject)
            elif "$subject" in val:
                continue  # Skip field if no subject
            val = val.replace("$statement", statement)
            draft[key] = val
        return draft


# =============================================================================
# Shared helpers
# =============================================================================

# Stopwords for filtering false-positive proper nouns
CAPTURE_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "but",
    "or",
    "for",
    "nor",
    "yet",
    "so",
    "it",
    "this",
    "that",
    "these",
    "those",
    "he",
    "she",
    "they",
    "we",
    "you",
    "i",
    "his",
    "her",
    "their",
    "its",
    "my",
    "your",
    "our",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "said",
    "asked",
    "replied",
    "told",
    "thought",
    "then",
    "now",
    "here",
    "there",
    "when",
    "where",
    "why",
    "how",
    "yes",
    "no",
    "not",
    "just",
    "only",
    "even",
    "also",
    "still",
    "all",
    "each",
    "every",
    "both",
    "few",
    "more",
    "most",
    "other",
    "one",
    "two",
    "three",
    "first",
    "second",
    "last",
    "next",
    "new",
    "old",
    "long",
    "little",
    "own",
    "same",
    "right",
    "big",
    "chapter",
    "part",
    "book",
    "section",
    "page",
    "character",
    "rule",
    "world",
    "backstory",
    "magic",
    "law",
    "forbidden",
    "never",
    "always",
    "cannot",
    "everyone",
    "nobody",
    "nothing",
    "everything",
    "something",
    "someone",
    "anyone",
    "anything",
    "today",
    "tomorrow",
    "yesterday",
    "maybe",
    "probably",
    "usually",
    "however",
    "although",
    "because",
    "therefore",
    "mr",
    "mrs",
    "ms",
    "dr",
    "sir",
    "lord",
    "lady",
    "king",
    "queen",
    "prince",
    "princess",
    # Code-mode additions
    "todo",
    "fixme",
    "hack",
    "note",
    "bug",
    "fix",
    "let",
    "use",
    "add",
    "implement",
    "build",
    "refactor",
    "test",
    "run",
    "try",
    "check",
}

_PROPER_NOUN_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b")

# Question detection
_QUESTION_RE = re.compile(r"\?\s*$")

# Hypotheticals — hard skip
_HYPOTHETICAL_RE = re.compile(
    r"^\s*(?:what if|suppose|imagine|let's say|maybe|perhaps|I wonder|could we|should we|how about)\b",
    re.IGNORECASE,
)

# Meta-discussion — hard skip
_META_RE = re.compile(
    r"^\s*(?:I['\u2019]m thinking (?:of|about)|I want to make|let me think|"
    r"do you think|I['\u2019]m not sure if|we could have|"
    r"I['\u2019]m considering|I might want|I['\u2019]d like to try)\b",
    re.IGNORECASE,
)

# Question tag patterns (stripped before re-testing as assertion)
_QUESTION_TAG_RE = re.compile(
    r",?\s*(?:right|yeah|no|correct|ok|okay|huh|eh|"
    r"isn't\s+\w+|aren't\s+\w+|don't\s+\w+|doesn't\s+\w+|"
    r"wouldn't\s+\w+|won't\s+\w+|can't\s+\w+|hasn't\s+\w+)\s*\??\s*$",
    re.IGNORECASE,
)


def classify_skippable(text: str) -> str | None:
    """Classify whether text should be skipped and why.

    Returns:
        None if text is scannable.
        'question' if text ends with '?'.
        'hypothetical' if text starts with hypothetical language.
        'meta' if text is meta-discussion.
    """
    stripped = text.strip()
    if _HYPOTHETICAL_RE.match(stripped):
        return "hypothetical"
    if _META_RE.match(stripped):
        return "meta"
    if _QUESTION_RE.search(stripped):
        return "question"
    return None


def strip_question_tag(text: str) -> str | None:
    """Strip trailing question tag from an assertion.

    'Zara is from Ashenmere, right?' → 'Zara is from Ashenmere'

    Returns stripped text, or None if not a question-tagged assertion.
    """
    stripped = text.strip()
    result = _QUESTION_TAG_RE.sub("", stripped).strip()
    if result and result != stripped and not result.endswith("?"):
        return result
    return None


def extract_proper_noun(text: str) -> str | None:
    """Extract the most likely proper noun from text.

    Filters out stopwords and common false positives.
    Returns None if no plausible proper noun found.
    """
    matches = _PROPER_NOUN_RE.findall(text)
    for name in matches:
        if name.lower() in CAPTURE_STOPWORDS:
            continue
        if len(name) < 2:
            continue
        return name
    return None


def split_into_statements(text: str) -> list[dict[str, Any]]:
    """Split text into statements, tracking character offsets.

    Spans index into the EXACT input string — no normalization.
    """
    statements: list[dict[str, Any]] = []
    pos = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            # Find the actual position of this stripped content in the original
            line_start = text.find(stripped, pos)
            if line_start == -1:
                line_start = pos

            # Split on sentence boundaries (". " before uppercase)
            sub_pos = 0
            parts = re.split(r"(?<=\.)\s+(?=[A-Z])", stripped)
            for part in parts:
                part_offset = text.find(part, line_start + sub_pos)
                if part_offset == -1:
                    part_offset = line_start + sub_pos
                statements.append(
                    {
                        "text": part,
                        "offset": part_offset,
                    }
                )
                sub_pos += len(part) + 1
        pos += len(line) + 1
    return statements


def dedup_captures(items: list[CapturedItem]) -> list[CapturedItem]:
    """Merge captures with same kind+subject_guess.

    Keeps highest confidence, merges spans, concatenates statements.
    When subject_guess is absent, falls back to statement text for the key
    so that distinct source refs (e.g., two different DOIs) stay separate.
    """
    seen: dict[str, CapturedItem] = {}

    for item in items:
        subject_key = (item.subject_guess or "").lower()
        if not subject_key:
            # No subject → use statement as identity (prevents merging distinct refs)
            subject_key = item.statement.strip().lower()
        key = f"{item.kind}:{subject_key}"

        if key in seen:
            existing = seen[key]
            if item.confidence > existing.confidence:
                existing.confidence = item.confidence
                existing.subject_source = item.subject_source
            existing.evidence_spans.extend(item.evidence_spans)
            if item.statement not in existing.statement:
                existing.statement = f"{existing.statement}; {item.statement}"
            for k, v in item.draft_payload.items():
                if k not in existing.draft_payload:
                    existing.draft_payload[k] = v
                elif k == "description" and v not in existing.draft_payload[k]:
                    existing.draft_payload[k] = f"{existing.draft_payload[k]}; {v}"
        else:
            seen[key] = item

    return list(seen.values())


# =============================================================================
# Code Capture Classifier
# =============================================================================


class CodeCaptureKind(str, Enum):
    """Kinds of structured intent captured in code-mode chat."""

    WORK_ITEM = "work_item"
    """Task, TODO, or action item."""

    HYPOTHESIS = "hypothesis"
    """Bug hypothesis or root cause guess."""

    DECISION = "decision"
    """Architectural or design decision."""

    PATCH_INTENT = "patch_intent"
    """Structured intent to change code (not a diff)."""


def _code_patterns() -> list[PatternDef]:
    """Compile code-mode capture patterns."""
    patterns: list[PatternDef] = []

    # --- WORK_ITEM (explicit markers) ---

    patterns.append(
        PatternDef(
            name="todo_prefix",
            category="explicit_marker",
            regex=re.compile(
                r"(?i)^(?:TODO|FIXME|HACK)\s*:?\s*(.+)",
                re.MULTILINE,
            ),
            kind=CodeCaptureKind.WORK_ITEM,
            confidence=0.92,
            field_guess="task",
            subject_group=None,
            statement_group=0,
            draft_fields={"task": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="need_to",
            category="work_marker",
            regex=re.compile(
                r"(?i)(?:we\s+)?(?:need|have)\s+to\s+(.+?)(?:\.|$)",
            ),
            kind=CodeCaptureKind.WORK_ITEM,
            confidence=0.68,
            field_guess="task",
            subject_group=None,
            statement_group=0,
            draft_fields={"task": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="lets_verb",
            category="work_marker",
            regex=re.compile(
                r"(?i)let'?s\s+(refactor|rewrite|fix|clean\s+up|update|migrate|move|split|extract|remove|delete|rename)\s+(.+?)(?:\.|$)",
            ),
            kind=CodeCaptureKind.WORK_ITEM,
            confidence=0.72,
            field_guess="task",
            subject_group=None,
            statement_group=None,
            draft_fields={"task": "$statement"},
        )
    )

    # --- HYPOTHESIS (bug/root cause) ---

    patterns.append(
        PatternDef(
            name="bug_is_in",
            category="hypothesis_marker",
            regex=re.compile(
                r"(?i)(?:the\s+)?(?:bug|issue|problem|crash|error|failure)\s+(?:is|seems?\s+to\s+be|might\s+be|could\s+be)\s+(?:in\s+)?(.+?)(?:\.|$)",
            ),
            kind=CodeCaptureKind.HYPOTHESIS,
            confidence=0.72,
            field_guess="description",
            subject_group=None,
            statement_group=0,
            draft_fields={"description": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="i_think_cause",
            category="hypothesis_marker",
            regex=re.compile(
                r"(?i)(?:I\s+think|I\s+suspect|I\s+bet)\s+(?:it'?s?\s+)?(?:because|due\s+to|caused\s+by)\s+(.+?)(?:\.|$)",
            ),
            kind=CodeCaptureKind.HYPOTHESIS,
            confidence=0.65,
            field_guess="description",
            subject_group=None,
            statement_group=0,
            draft_fields={"description": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="hypothesis_colon",
            category="explicit_marker",
            regex=re.compile(
                r"(?i)^hypothesis\s*:\s*(.+)",
                re.MULTILINE,
            ),
            kind=CodeCaptureKind.HYPOTHESIS,
            confidence=0.90,
            field_guess="description",
            subject_group=None,
            statement_group=0,
            draft_fields={"description": "$statement"},
        )
    )

    # --- DECISION (architectural choices) ---

    patterns.append(
        PatternDef(
            name="should_use",
            category="decision_marker",
            regex=re.compile(
                r"(?i)(?:we\s+)?(?:should|will|gonna|going\s+to)\s+use\s+(.+?)(?:\s+(?:for|instead|because|since)\s+.+?)?(?:\.|$)",
            ),
            kind=CodeCaptureKind.DECISION,
            confidence=0.70,
            field_guess="choice",
            subject_group=None,
            statement_group=None,
            draft_fields={"choice": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="decision_colon",
            category="explicit_marker",
            regex=re.compile(
                r"(?i)^decision\s*:\s*(.+)",
                re.MULTILINE,
            ),
            kind=CodeCaptureKind.DECISION,
            confidence=0.92,
            field_guess="choice",
            subject_group=None,
            statement_group=0,
            draft_fields={"choice": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="api_should",
            category="decision_marker",
            regex=re.compile(
                r"(?i)(?:the\s+)?(?:API|endpoint|interface|schema|database|service)\s+should\s+(.+?)(?:\.|$)",
            ),
            kind=CodeCaptureKind.DECISION,
            confidence=0.72,
            field_guess="choice",
            subject_group=None,
            statement_group=None,
            draft_fields={"choice": "$statement"},
        )
    )

    # --- PATCH_INTENT (structured change intent) ---

    patterns.append(
        PatternDef(
            name="add_verb",
            category="intent_marker",
            regex=re.compile(
                r"(?i)(?:add|implement|build|create|wire\s+up)\s+(.+?)(?:\s+(?:to|in|for|at)\s+(.+?))?(?:\.|$)",
            ),
            kind=CodeCaptureKind.PATCH_INTENT,
            confidence=0.65,
            field_guess="intent",
            subject_group=None,
            statement_group=None,
            draft_fields={"intent": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="change_to",
            category="intent_marker",
            regex=re.compile(
                r"(?i)(?:change|switch|replace|swap)\s+(.+?)\s+(?:to|with|for)\s+(.+?)(?:\.|$)",
            ),
            kind=CodeCaptureKind.PATCH_INTENT,
            confidence=0.68,
            field_guess="intent",
            subject_group=None,
            statement_group=None,
            draft_fields={"intent": "$statement"},
        )
    )

    return patterns


_CODE_PATTERNS = _code_patterns()


class CodeCaptureClassifier(CaptureClassifier):
    """Detect work items, hypotheses, decisions, and patch intents in code chat."""

    def _get_version(self) -> str:
        return "code@1.0.0"

    def _get_patterns(self) -> list[PatternDef]:
        return _CODE_PATTERNS


# =============================================================================
# Research Capture Classifier
# =============================================================================


class ResearchCaptureKind(str, Enum):
    """Kinds of structured intent captured in research-mode chat."""

    CLAIM = "claim"
    """Factual or causal assertion."""

    CITATION = "citation"
    """Reference to source material."""

    EXPERIMENT = "experiment"
    """Experiment idea or test plan."""

    ASSUMPTION = "assumption"
    """Stated assumption or simplification."""


def _research_patterns() -> list[PatternDef]:
    """Compile research-mode capture patterns."""
    patterns: list[PatternDef] = []

    # --- CLAIM (causal/factual assertions) ---

    patterns.append(
        PatternDef(
            name="causes",
            category="claim_marker",
            regex=re.compile(
                r"(?i)\b(.+?)\s+(?:causes|leads\s+to|results\s+in|produces|correlates\s+with)\s+(.+?)(?:\.|$)",
            ),
            kind=ResearchCaptureKind.CLAIM,
            confidence=0.72,
            field_guess="assertion",
            subject_group=None,
            statement_group=None,
            draft_fields={"assertion": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="claim_colon",
            category="explicit_marker",
            regex=re.compile(
                r"(?i)^(?:claim|finding|observation|conclusion)\s*:\s*(.+)",
                re.MULTILINE,
            ),
            kind=ResearchCaptureKind.CLAIM,
            confidence=0.90,
            field_guess="assertion",
            subject_group=None,
            statement_group=0,
            draft_fields={"assertion": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="is_known",
            category="claim_marker",
            regex=re.compile(
                r"(?i)(?:it\s+is\s+known|it'?s\s+established|evidence\s+(?:shows|suggests|indicates))\s+(?:that\s+)?(.+?)(?:\.|$)",
            ),
            kind=ResearchCaptureKind.CLAIM,
            confidence=0.68,
            field_guess="assertion",
            subject_group=None,
            statement_group=0,
            draft_fields={"assertion": "$statement"},
        )
    )

    # --- CITATION (source references) ---

    patterns.append(
        PatternDef(
            name="per_source",
            category="citation_marker",
            regex=re.compile(
                r"(?i)(?:per|according\s+to|as\s+(?:shown|described|noted)\s+(?:by|in))\s+(.+?)(?:,\s+|\.\s*$)",
            ),
            kind=ResearchCaptureKind.CITATION,
            confidence=0.78,
            field_guess="source",
            subject_group=0,
            statement_group=None,
            draft_fields={"source": "$subject", "context": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="author_showed",
            category="citation_marker",
            regex=re.compile(
                r"\b([A-Z][a-z]+(?:\s+(?:et\s+al|and\s+[A-Z][a-z]+))?\.?)\s+(?:\(\d{4}\)\s+)?(?:showed|demonstrated|found|argued|proposed|reported)\s+(?:that\s+)?(.+?)(?:\.|$)",
            ),
            kind=ResearchCaptureKind.CITATION,
            confidence=0.75,
            field_guess="source",
            subject_group=0,
            statement_group=1,
            draft_fields={"source": "$subject", "finding": "$statement"},
        )
    )

    # --- EXPERIMENT (test plans) ---

    patterns.append(
        PatternDef(
            name="try_test",
            category="experiment_marker",
            regex=re.compile(
                r"(?i)(?:try|test|run|measure|benchmark|profile)\s+(?:whether|if|how)\s+(.+?)(?:\.|$)",
            ),
            kind=ResearchCaptureKind.EXPERIMENT,
            confidence=0.70,
            field_guess="description",
            subject_group=None,
            statement_group=0,
            draft_fields={"description": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="experiment_colon",
            category="explicit_marker",
            regex=re.compile(
                r"(?i)^(?:experiment|ablation|test\s+plan)\s*:\s*(.+)",
                re.MULTILINE,
            ),
            kind=ResearchCaptureKind.EXPERIMENT,
            confidence=0.90,
            field_guess="description",
            subject_group=None,
            statement_group=0,
            draft_fields={"description": "$statement"},
        )
    )

    # --- ASSUMPTION (stated assumptions) ---

    patterns.append(
        PatternDef(
            name="assuming",
            category="assumption_marker",
            regex=re.compile(
                r"(?i)(?:assuming|let'?s\s+assume|given\s+that|granted\s+that)\s+(.+?)(?:\.|$)",
            ),
            kind=ResearchCaptureKind.ASSUMPTION,
            confidence=0.75,
            field_guess="assumption",
            subject_group=None,
            statement_group=0,
            draft_fields={"assumption": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="assumption_colon",
            category="explicit_marker",
            regex=re.compile(
                r"(?i)^assumption\s*:\s*(.+)",
                re.MULTILINE,
            ),
            kind=ResearchCaptureKind.ASSUMPTION,
            confidence=0.92,
            field_guess="assumption",
            subject_group=None,
            statement_group=0,
            draft_fields={"assumption": "$statement"},
        )
    )

    # --- SOURCE_REF (structured identifiers: DOI, PyPI, CVE, RFC, arXiv) ---
    # These use statement_group (not subject_group) because identifiers are
    # technical strings, not proper nouns — extract_subject requires uppercase.

    patterns.append(
        PatternDef(
            name="doi_ref",
            category="source_ref",
            regex=re.compile(
                r"(?:doi:\s*|DOI:\s*|https?://doi\.org/)(10\.\d{4,}/[^\s,;)\]]+)",
            ),
            kind=ResearchCaptureKind.CITATION,
            confidence=0.95,
            field_guess="source_ref",
            subject_group=None,
            statement_group=0,
            draft_fields={"source_ref": "doi:$statement", "ref_type": "doi"},
        )
    )

    patterns.append(
        PatternDef(
            name="pypi_ref",
            category="source_ref",
            regex=re.compile(
                r"(?:pypi:|pip\s+install\s+)([a-zA-Z0-9][\w.-]*(?:[>=<]=?\d[\w.]*)?)",
            ),
            kind=ResearchCaptureKind.CITATION,
            confidence=0.92,
            field_guess="source_ref",
            subject_group=None,
            statement_group=0,
            draft_fields={"source_ref": "pypi:$statement", "ref_type": "pypi"},
        )
    )

    patterns.append(
        PatternDef(
            name="cve_ref",
            category="source_ref",
            regex=re.compile(
                r"(CVE-\d{4}-\d{4,})",
            ),
            kind=ResearchCaptureKind.CITATION,
            confidence=0.95,
            field_guess="source_ref",
            subject_group=None,
            statement_group=0,
            draft_fields={"source_ref": "cve:$statement", "ref_type": "cve"},
        )
    )

    patterns.append(
        PatternDef(
            name="rfc_ref",
            category="source_ref",
            regex=re.compile(
                r"(?i)RFC\s*(\d{3,})",
            ),
            kind=ResearchCaptureKind.CITATION,
            confidence=0.90,
            field_guess="source_ref",
            subject_group=None,
            statement_group=0,
            draft_fields={"source_ref": "rfc:$statement", "ref_type": "rfc"},
        )
    )

    patterns.append(
        PatternDef(
            name="arxiv_ref",
            category="source_ref",
            regex=re.compile(
                r"(?:arXiv:|arxiv\.org/abs/)(\d{4}\.\d{4,})",
            ),
            kind=ResearchCaptureKind.CITATION,
            confidence=0.95,
            field_guess="source_ref",
            subject_group=None,
            statement_group=0,
            draft_fields={"source_ref": "arxiv:$statement", "ref_type": "arxiv"},
        )
    )

    return patterns


_RESEARCH_PATTERNS = _research_patterns()


class ResearchCaptureClassifier(CaptureClassifier):
    """Detect claims, citations, experiments, and assumptions in research chat."""

    def _get_version(self) -> str:
        return "research@1.1.0"

    def _get_patterns(self) -> list[PatternDef]:
        return _RESEARCH_PATTERNS


# =============================================================================
# Registry
# =============================================================================


def get_classifier(mode: str, **kwargs) -> CaptureClassifier:
    """Get the capture classifier for a governor mode.

    Args:
        mode: Governor mode ('fiction', 'code', 'research', 'nonfiction').
        **kwargs: Passed to classifier constructor.

    Returns:
        CaptureClassifier instance.

    Raises:
        ValueError: If mode has no registered classifier.
    """
    # Lazy import to avoid circular dependency
    if mode == "fiction":
        from fiction_governor.canon_capture import CanonCaptureClassifier

        return CanonCaptureClassifier(**kwargs)
    elif mode == "code":
        return CodeCaptureClassifier(**kwargs)
    elif mode in ("research", "nonfiction"):
        return ResearchCaptureClassifier(**kwargs)
    else:
        raise ValueError(f"No capture classifier for mode: {mode!r}")
