# SPDX-License-Identifier: Apache-2.0
"""The boundary between noticing an interpretation and rewriting canon.

These cases are built from a real incident. While debugging a project's canon,
an analysis pass repeatedly offered its own readings of the author's world rules
as defects needing repair: it disputed the scope of "all robots", read "since
robots have never been human they cannot perceive ghosts" as making having-been-
human *sufficient* for perception, and then produced human characters with no
ghost affinity as counterexamples to a claim the author never made. The author
had to reject each reading in turn.

Two findings in that same pass were real, and both were mechanical: a world fact
stored under a prohibition category, and a promotion that dropped its subject.

The invariant: a model may notice an interpretation. It may not promote that
interpretation into authority over the author's canon.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gov_webui.canon_review_store import (
    DIAGNOSTIC_ONLY_WARRANTS,
    MUTATION_ADMISSIBLE_WARRANTS,
    CanonResolutionStandsError,
    CanonReviewStore,
    evidence_fingerprint,
)


@pytest.fixture()
def store(tmp_path: Path) -> CanonReviewStore:
    return CanonReviewStore(tmp_path / "canon-review.json", project_id="doverton")


# =============================================================================
# The two warrant classes are disjoint and mean opposite things
# =============================================================================


def test_warrant_classes_are_disjoint() -> None:
    assert not (MUTATION_ADMISSIBLE_WARRANTS & DIAGNOSTIC_ONLY_WARRANTS)


def test_an_interpretation_cannot_authorize_a_canon_edit(store: CanonReviewStore) -> None:
    """The incident's central failure, stated as a property."""
    for warrant in sorted(DIAGNOSTIC_ONLY_WARRANTS):
        candidate = store.add(
            kind="world_rule",
            subject="robots",
            statement=f"a reading reached via {warrant}",
            warrant=warrant,
            target_anchor_id="forbid-1",
        )
        assert candidate.mutation_admissible is False


def test_source_fidelity_findings_remain_actionable(store: CanonReviewStore) -> None:
    """The boundary must not make the debugger timid about provable defects."""
    for warrant in sorted(MUTATION_ADMISSIBLE_WARRANTS - {"author_statement"}):
        candidate = store.add(
            kind="world_rule",
            subject="Halo",
            statement=f"a defect shown against the source via {warrant}",
            warrant=warrant,
            target_anchor_id="rule-1",
        )
        assert candidate.mutation_admissible is True


# =============================================================================
# The specific strengthening that started the incident
# =============================================================================


def test_implication_is_not_strengthened_into_sufficiency(store: CanonReviewStore) -> None:
    """Source states an exclusion. Sufficiency is the model's addition.

    "Since robots have never been human, they cannot perceive ghosts" gives a
    necessary condition at most. Reading it as "having been human is sufficient
    for perceiving ghosts", then citing a human who perceives nothing, argues
    against a proposition the author never wrote.
    """
    candidate = store.add(
        kind="world_rule",
        subject="ghost perception",
        statement=(
            "the rule is defective: a human character has no ghost affinity, "
            "which contradicts having-been-human enabling perception"
        ),
        warrant="strengthened_proposition",
        target_anchor_id="forbid-1",
        relied_on=["having been human is sufficient for perceiving ghosts"],
    )

    assert candidate.mutation_admissible is False
    assert candidate.warrant in DIAGNOSTIC_ONLY_WARRANTS
    # It survives as something the author can read and answer.
    assert store.get(candidate.id).statement == candidate.statement


def test_a_proposal_targeting_an_anchor_needs_a_reason(store: CanonReviewStore) -> None:
    """Rewriting stored canon is a claim about it, not a bare author statement."""
    with pytest.raises(ValueError, match="source-fidelity warrant"):
        store.add(
            kind="world_rule",
            subject="robots",
            statement="reworded rule",
            target_anchor_id="forbid-1",
        )


def test_unknown_warrants_are_refused(store: CanonReviewStore) -> None:
    with pytest.raises(ValueError, match="unknown canon review warrant"):
        store.add(kind="world_rule", statement="x", warrant="seems_wrong_to_me")


# =============================================================================
# Author resolutions stick
# =============================================================================


def test_a_dismissed_reading_is_not_reopened_from_identical_evidence(
    store: CanonReviewStore,
) -> None:
    """The author answered "all robots". A later pass must not re-ask."""
    first = store.add(
        kind="world_rule",
        subject="robots",
        statement="the rule should apply only to the robots currently named",
        warrant="scope_interpretation",
        target_anchor_id="forbid-1",
    )
    store.resolve(first.id, status="dismissed")

    with pytest.raises(CanonResolutionStandsError, match="already dismissed"):
        store.add(
            kind="world_rule",
            subject="robots",
            statement="the rule should apply only to the robots currently named",
            warrant="scope_interpretation",
            target_anchor_id="forbid-1",
        )


def test_reaching_the_same_claim_by_another_route_does_not_reopen_it(
    store: CanonReviewStore,
) -> None:
    """Stickiness is keyed to the evidence, not to the argument made from it."""
    first = store.add(
        kind="world_rule",
        subject="robots",
        statement="the rule should apply only to the robots currently named",
        warrant="scope_interpretation",
        target_anchor_id="forbid-1",
    )
    store.resolve(first.id, status="dismissed")

    with pytest.raises(CanonResolutionStandsError):
        store.add(
            kind="world_rule",
            subject="robots",
            statement="  The rule should apply ONLY to the robots currently named  ",
            warrant="inferred_implication",  # different route, same claim
            target_anchor_id="forbid-1",
        )


def test_genuinely_new_evidence_may_still_be_raised(store: CanonReviewStore) -> None:
    """A closed question stays closed; a different one does not."""
    first = store.add(
        kind="world_rule",
        subject="robots",
        statement="the rule should apply only to the robots currently named",
        warrant="scope_interpretation",
        target_anchor_id="forbid-1",
    )
    store.resolve(first.id, status="dismissed")

    later = store.add(
        kind="world_rule",
        subject="robots",
        statement="a newly written scene states that one robot sees a ghost",
        warrant="contradictory_state",
        target_anchor_id="forbid-1",
    )
    assert later.status == "pending"


def test_fingerprint_ignores_the_warrant_and_normalises_whitespace() -> None:
    a = evidence_fingerprint(kind="world_rule", subject="Robots", statement="x  y", target="f-1")
    b = evidence_fingerprint(kind="world_rule", subject="robots", statement=" x y ", target="f-1")
    assert a == b


# =============================================================================
# Mixed pass: mechanical findings survive, interpretive ones are suppressed
# =============================================================================


def test_a_mixed_pass_keeps_the_provable_and_suppresses_the_interpretive(
    store: CanonReviewStore,
) -> None:
    """Exactly the shape of the incident: two real defects, three readings."""
    findings = [
        ("category_misclassification", "a world fact is stored under a prohibition category"),
        ("dropped_subject", "promotion dropped the subject 'Halo:' from this rule"),
        ("scope_interpretation", "'all robots' probably means only the named robots"),
        ("strengthened_proposition", "having been human should be sufficient for perception"),
        ("ontology_clarification", "ghosts should be described as people rather than remains"),
    ]
    candidates = [
        store.add(
            kind="world_rule",
            subject="doverton",
            statement=statement,
            warrant=warrant,
            target_anchor_id="forbid-1" if "prohibition" in statement else "rule-1",
        )
        for warrant, statement in findings
    ]

    actionable = [c.statement for c in candidates if c.mutation_admissible]
    diagnostics = [c.statement for c in candidates if not c.mutation_admissible]

    assert actionable == [
        "a world fact is stored under a prohibition category",
        "promotion dropped the subject 'Halo:' from this rule",
    ]
    assert len(diagnostics) == 3
    # Nothing is silently discarded; the author still sees every reading.
    assert len(store.list(status="pending")) == 5


# =============================================================================
# Records written before warrants existed
# =============================================================================


def test_existing_records_load_as_author_statements(tmp_path: Path) -> None:
    """Prior candidates all came from prose capture, which is authorial."""
    path = tmp_path / "canon-review.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "next_sequence": 2,
                "updated_at": "2026-09-01T00:00:00+00:00",
                "items": {
                    "cap-1": {
                        "id": "cap-1",
                        "project_id": "doverton",
                        "kind": "character",
                        "statement": "Jinwoo: considerate",
                        "status": "pending",
                        "created_at": "2026-09-01T00:00:00+00:00",
                        "updated_at": "2026-09-01T00:00:00+00:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    item = CanonReviewStore(path, project_id="doverton").get("cap-1")
    assert item.warrant == "author_statement"
    assert item.mutation_admissible is True
    assert item.target_anchor_id is None
