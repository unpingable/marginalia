# SPDX-License-Identifier: Apache-2.0
"""The observed cast does not close the ontology.

A rule stated over a category stays a rule over the category even when the pages
written so far show one member of it. Fiction withholds ontology deliberately —
the subtype introduced later refines the world model rather than proving the
earlier generic rule was overbroad — so what retrieval can show is:

    observed_members(C) subset-of C

and never, on its own:

    observed_members(C) == C

Marginalia does not model the category system. It asks one question of the
author's own canon: has the author said this category is complete?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from gov_webui.canon_review_store import CanonReviewStore
from gov_webui.canon_scope import (
    category_is_closed,
    closure_statements,
    supersession_statements,
)


@dataclass
class FakeAnchor:
    id: str
    description: str
    forbidden_patterns: list[str] = field(default_factory=list)


def anchors(*pairs: tuple[str, str]) -> list[FakeAnchor]:
    return [FakeAnchor(id=anchor_id, description=text) for anchor_id, text in pairs]


@pytest.fixture()
def store(tmp_path: Path) -> CanonReviewStore:
    return CanonReviewStore(tmp_path / "canon-review.json", project_id="doverton")


# =============================================================================
# Closure authority is explicit metadata, never inferred from prose
# =============================================================================


def test_observed_members_do_not_close_a_category() -> None:
    assert category_is_closed({}, "robots") is False
    assert closure_statements({}, "robots") == []


def test_closure_language_alone_grants_no_authority() -> None:
    canon = anchors(("world-2", "These are the only robots: Jacqueline and Misty."))
    assert canon[0].description.startswith("These are the only")
    assert category_is_closed({}, "robots") is False


def test_explicit_author_metadata_closes_only_its_named_category() -> None:
    closures = {"world-2": "robots"}
    assert closure_statements(closures, "robots") == ["world-2"]
    assert category_is_closed(closures, "robots") is True
    assert category_is_closed(closures, "ghosts") is False


def test_closure_metadata_normalises_case_and_whitespace_only() -> None:
    closures = {"world-2": "  Synthetic   Beings "}
    assert category_is_closed(closures, "synthetic beings") is True
    assert category_is_closed(closures, "synthetic being") is False


# =============================================================================
# Narrative order is not world-time order
# =============================================================================


def test_registration_order_does_not_retire_an_earlier_anchor() -> None:
    """A later anchor is not a newer truth."""
    canon = anchors(
        ("world-1", "AI may not retain a human form outside hostilities."),
        # Entered later, describes earlier world-time: the history behind world-1.
        (
            "world-2",
            "Historically, AI was deployed as human-form soldiers, and postwar law "
            "prohibited civilian retention of those form factors.",
        ),
    )
    assert supersession_statements(canon, "world-1") == []


def test_explicit_retirement_naming_the_anchor_is_recognised() -> None:
    canon = anchors(
        ("world-1", "AI may not retain a human form outside hostilities."),
        ("world-2", "world-1 no longer applies after the amnesty."),
    )
    assert supersession_statements(canon, "world-1") == ["world-2"]


@pytest.mark.parametrize(
    "text",
    [
        "This rule supersedes world-1.",
        "world-1 is superseded by the amnesty statute.",
        "world-1 no longer holds.",
        "Apply this instead of world-1.",
    ],
)
def test_supersession_phrasings(text: str) -> None:
    canon = anchors(("world-1", "An earlier rule."), ("world-9", text))
    assert supersession_statements(canon, "world-1") == ["world-9"]


def test_an_anchor_does_not_supersede_itself() -> None:
    canon = anchors(("world-1", "world-1 no longer applies."))
    assert supersession_statements(canon, "world-1") == []
