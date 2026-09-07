# SPDX-License-Identifier: Apache-2.0
"""Whether the author has closed a category, or only shown some of it.

Fiction withholds ontology on purpose. A rule stated over a category stays a
rule over the category even when the pages so far show one member of it, because
the next chapter may introduce a subtype, a hidden population, or the history
that explains the rule. So the observed cast is evidence about the story, not
about the world:

    observed_members(C) subset-of C     is what retrieval can show
    observed_members(C) == C            is a further claim, and needs saying

Marginalia does not model the category system. It only answers one question,
from the author's own canon: has the author said this category is complete? If
not, nothing may reason from its current extent. That keeps the safe default
without pretending to do natural-language semantics.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


# Closure is authority metadata recorded by an explicit author action. Natural-language
# patterns may be useful diagnostics later, but they cannot grant closed-world authority.
def closure_statements(closures: Mapping[str, str], category: str) -> list[str]:
    """Anchor ids explicitly marked by the author as closing the category."""
    target = " ".join(category.split()).casefold()
    return [
        anchor_id
        for anchor_id, closed in closures.items()
        if " ".join(closed.split()).casefold() == target
    ]


def category_is_closed(closures: Mapping[str, str], category: str) -> bool:
    return bool(closure_statements(closures, category))


# Canon is registered in the order the author happened to enter it, which is not
# the order the story tells and is not the order the world lived. A rule entered
# later may describe earlier world-time — backstory, a disclosed history, the
# legal regime that explains a rule stated chapters ago — so a newer anchor is
# not a newer truth. Supersession has to be said, never inferred from position.
_SUPERSESSION_TEMPLATES = (
    r"(?:supersedes|superseding|replaces|replacing|overrides|overriding)\s+{a}\b",
    r"{a}\s+(?:is|are)\s+(?:now\s+)?(?:superseded|replaced|overridden|retired|revoked)\b",
    r"{a}\s+no\s+longer\s+(?:applies|holds|stands)\b",
    r"(?:instead\s+of|rather\s+than)\s+{a}\b",
)


def supersession_statements(anchors: Any, superseded_id: str) -> list[str]:
    """Anchor ids that explicitly retire `superseded_id`.

    Naming the retired anchor is required. An author who means to replace a rule
    can say so; a pass that merely notices two anchors sitting near each other
    cannot say it for them.
    """
    target = re.escape(superseded_id)
    patterns = [
        re.compile(template.replace("{a}", target), re.IGNORECASE)
        for template in _SUPERSESSION_TEMPLATES
    ]
    return [
        anchor.id
        for anchor in anchors
        if anchor.id != superseded_id
        and any(pattern.search(getattr(anchor, "description", "") or "") for pattern in patterns)
    ]
