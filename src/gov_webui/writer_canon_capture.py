# SPDX-License-Identifier: Apache-2.0
"""
Canon capture: detect definition-ish statements in fiction chat.

Fiction-specific classifier built on the generic capture protocol
in ``governor.capture``. Detects characters, world rules, relationships,
and constraints with conservative pattern matching.

Errs toward missing definitions rather than flagging narration.
Never auto-promotes — user must explicitly accept.
"""

import re
from enum import Enum

from gov_webui.writer_capture_base import (
    CaptureClassifier,
    CapturedItem,
    CaptureReceipt,
    CaptureStatus,
    PatternDef,
    # Re-export for backward compat
    classify_skippable as _classify_skippable,
    extract_proper_noun as _base_extract_proper_noun,
)

# Re-export base types so existing imports keep working
__all__ = [
    "CaptureKind",
    "CaptureStatus",
    "CapturedItem",
    "CaptureReceipt",
    "CanonCaptureClassifier",
]


# =============================================================================
# Fiction-specific capture kinds
# =============================================================================


class CaptureKind(str, Enum):
    """Kind of canonical fact captured from fiction chat."""

    CHARACTER = "character"
    WORLD_RULE = "world_rule"
    RELATIONSHIP = "relationship"
    CONSTRAINT = "constraint"


# =============================================================================
# Fiction capture patterns
# =============================================================================


def _fiction_patterns() -> list[PatternDef]:
    """Compile all fiction capture patterns.

    Ordered by specificity. Explicit markers (high confidence) first,
    then copula definitions, relationships, world-building, constraints.
    """
    patterns: list[PatternDef] = []

    # --- EXPLICIT_MARKERS (high confidence: 0.90) ---

    patterns.append(
        PatternDef(
            name="explicit_character",
            category="explicit_marker",
            regex=re.compile(r"(?i)^(?:character\s*:\s*)(.+)", re.MULTILINE),
            kind=CaptureKind.CHARACTER,
            confidence=0.90,
            field_guess="description",
            subject_group=None,
            statement_group=0,
            draft_fields={"description": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="explicit_rule",
            category="explicit_marker",
            regex=re.compile(r"(?i)^(?:(?:world\s+)?rule\s*:\s*)(.+)", re.MULTILINE),
            kind=CaptureKind.WORLD_RULE,
            confidence=0.90,
            field_guess="rule",
            subject_group=None,
            statement_group=0,
            draft_fields={"rule": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="explicit_backstory",
            category="explicit_marker",
            regex=re.compile(r"(?i)^(?:backstory\s*:\s*)(.+)", re.MULTILINE),
            kind=CaptureKind.CHARACTER,
            confidence=0.90,
            field_guess="description",
            subject_group=None,
            statement_group=0,
            draft_fields={"description": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="possessive_backstory",
            category="explicit_marker",
            regex=re.compile(
                r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'s\s+backstory\s+is\s+(.+?)(?:\.|$)",
                re.MULTILINE,
            ),
            kind=CaptureKind.CHARACTER,
            confidence=0.88,
            field_guess="description",
            subject_group=0,
            statement_group=1,
            draft_fields={"name": "$subject", "description": "$statement"},
        )
    )

    # --- COPULA_DEFINITIONS (medium confidence: 0.70) ---

    patterns.append(
        PatternDef(
            name="copula_is_descriptor",
            category="copula_definition",
            regex=re.compile(
                r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+is\s+(?:from\s+|a\s+|an\s+|the\s+)?([a-z].{3,80}?)(?:\.|,\s|$)",
            ),
            kind=CaptureKind.CHARACTER,
            confidence=0.70,
            field_guess="description",
            subject_group=0,
            statement_group=1,
            draft_fields={"name": "$subject", "description": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="copula_has_trait",
            category="copula_definition",
            regex=re.compile(
                r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+has\s+([a-z].{3,80}?)(?:\.|,\s|$)",
            ),
            kind=CaptureKind.CHARACTER,
            confidence=0.70,
            field_guess="description",
            subject_group=0,
            statement_group=1,
            draft_fields={"name": "$subject", "description": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="copula_was_origin",
            category="copula_definition",
            regex=re.compile(
                r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+was\s+(?:raised|born|trained|from)\s+(.{3,80}?)(?:\.|,\s|$)",
            ),
            kind=CaptureKind.CHARACTER,
            confidence=0.70,
            field_guess="description",
            subject_group=0,
            statement_group=1,
            draft_fields={"name": "$subject", "description": "$statement"},
        )
    )

    # --- RELATIONSHIP_MARKERS (medium confidence: 0.72) ---

    patterns.append(
        PatternDef(
            name="relationship_are",
            category="relationship_marker",
            regex=re.compile(
                r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+and\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+are\s+(.{3,60}?)(?:\.|,\s|$)",
            ),
            kind=CaptureKind.RELATIONSHIP,
            confidence=0.72,
            field_guess="description",
            subject_group=0,
            statement_group=None,
            draft_fields={"characters": "$subject", "description": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="relationship_possessive",
            category="relationship_marker",
            regex=re.compile(
                r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+is\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'s\s+(\w+(?:\s+\w+)?)\b",
            ),
            kind=CaptureKind.RELATIONSHIP,
            confidence=0.72,
            field_guess="description",
            subject_group=0,
            statement_group=None,
            draft_fields={"characters": "$subject", "description": "$statement"},
        )
    )

    # --- WORLD_BUILDING (medium confidence: 0.62-0.68) ---

    patterns.append(
        PatternDef(
            name="world_in_this_world",
            category="world_building",
            regex=re.compile(
                r"(?i)^(?:in\s+(?:this|the|our)\s+(?:world|story|setting|universe)\s*,?\s*)(.+?)(?:\.|$)",
                re.MULTILINE,
            ),
            kind=CaptureKind.WORLD_RULE,
            confidence=0.65,
            field_guess="rule",
            subject_group=None,
            statement_group=0,
            draft_fields={"rule": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="world_magic_works",
            category="world_building",
            regex=re.compile(
                r"(?i)(?:magic|technology|power)\s+(?:works|functions|operates)\s+(?:by|through|via)\s+(.+?)(?:\.|$)",
            ),
            kind=CaptureKind.WORLD_RULE,
            confidence=0.68,
            field_guess="rule",
            subject_group=None,
            statement_group=0,
            draft_fields={"rule": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="world_law_says",
            category="world_building",
            regex=re.compile(
                r"(?i)(?:the\s+law|the\s+rules?|custom|tradition)\s+(?:says?|requires?|forbids?|demands?)\s+(.+?)(?:\.|$)",
            ),
            kind=CaptureKind.WORLD_RULE,
            confidence=0.65,
            field_guess="rule",
            subject_group=None,
            statement_group=0,
            draft_fields={"rule": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="world_there_is_no",
            category="world_building",
            regex=re.compile(r"(?i)(?:there\s+(?:is|are)\s+no\s+)(.+?)(?:\.|$)"),
            kind=CaptureKind.WORLD_RULE,
            confidence=0.62,
            field_guess="rule",
            subject_group=None,
            statement_group=0,
            draft_fields={"rule": "$statement"},
        )
    )

    # --- CONSTRAINT_MARKERS (medium confidence: 0.68-0.75) ---

    patterns.append(
        PatternDef(
            name="constraint_would_never",
            category="constraint_marker",
            regex=re.compile(
                r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+would\s+never\s+(.+?)(?:\.|$)",
            ),
            kind=CaptureKind.CONSTRAINT,
            confidence=0.75,
            field_guess="wont",
            subject_group=0,
            statement_group=1,
            draft_fields={"name": "$subject", "wont": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="constraint_cannot",
            category="constraint_marker",
            regex=re.compile(
                r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:can'?t|cannot)\s+(.+?)(?:\.|$)",
            ),
            kind=CaptureKind.CONSTRAINT,
            confidence=0.72,
            field_guess="wont",
            subject_group=0,
            statement_group=1,
            draft_fields={"name": "$subject", "wont": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="constraint_always",
            category="constraint_marker",
            regex=re.compile(
                r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+always\s+(.+?)(?:\.|$)",
            ),
            kind=CaptureKind.CONSTRAINT,
            confidence=0.68,
            field_guess="description",
            subject_group=0,
            statement_group=1,
            draft_fields={"name": "$subject", "description": "$statement"},
        )
    )

    patterns.append(
        PatternDef(
            name="constraint_forbidden",
            category="constraint_marker",
            regex=re.compile(
                r"(?i)(?:it'?s\s+forbidden\s+to|no\s+one\s+(?:may|can|is\s+allowed\s+to))\s+(.+?)(?:\.|$)",
            ),
            kind=CaptureKind.WORLD_RULE,
            confidence=0.72,
            field_guess="rule",
            subject_group=None,
            statement_group=0,
            draft_fields={"rule": "$statement"},
        )
    )

    return patterns


_FICTION_PATTERNS = _fiction_patterns()


# =============================================================================
# Fiction Classifier
# =============================================================================


class CanonCaptureClassifier(CaptureClassifier):
    """Detect definition-ish statements in fiction chat messages.

    Returns CapturedItem objects (always PENDING) and a CaptureReceipt
    proving what patterns fired. Errs toward missing definitions rather
    than flagging narration. Never auto-promotes.
    """

    def _get_version(self) -> str:
        return "fiction.canon@1.0.0"

    def _get_patterns(self) -> list[PatternDef]:
        return _FICTION_PATTERNS


# Backward compat: expose helpers for code that imported them directly
def _is_skippable(text: str) -> bool:
    """Backward-compat wrapper. Use classify_skippable() from governor.capture."""
    return _classify_skippable(text) is not None


def _extract_proper_noun(text: str) -> str | None:
    """Backward-compat wrapper. Use extract_proper_noun() from governor.capture."""
    return _base_extract_proper_noun(text)
