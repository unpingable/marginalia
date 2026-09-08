# SPDX-License-Identifier: Apache-2.0
"""The application-owned system blocks every generation budget must count.

Generation sends these blocks with every governed turn, so anything that sizes a
context — admission, maintenance planning, or operator readiness reporting — has
to count exactly the same blocks. Keeping the construction here means those
callers cannot drift apart by each assembling their own approximation.
"""

from __future__ import annotations

import json
from pathlib import Path

from gov_webui.creative_project import CreativeProjectConfig, render_project_context


def accepted_canon_message(governor_dir: Path | str) -> dict[str, str] | None:
    """Render accepted canon as counted generation input, never derived summary.

    Entries are grouped by what they assert, because the three anchor types mean
    genuinely different things and a flat list leaves their polarity ambiguous.
    "Robots cannot see ghosts" is a fact the world obeys; "Time travel" is a
    thing not to write. Both are prohibitions in the registry, and under one
    heading a model has to guess which reading applies to which.
    """
    from gov_webui.writer_continuity import AnchorType, create_registry

    groups: dict[str, list[dict[str, object]]] = {
        "world_facts": [],
        "character_facts": [],
        "must_not_appear": [],
    }
    section_of = {
        AnchorType.DEFINITION: "world_facts",
        AnchorType.CANON: "character_facts",
        AnchorType.PROHIBITION: "must_not_appear",
    }
    for anchor in create_registry(governor_dir).all():
        section = section_of.get(anchor.anchor_type)
        if section is None:
            continue
        item: dict[str, object] = {"id": anchor.id, "description": anchor.description}
        patterns = getattr(anchor, "forbidden_patterns", None)
        if patterns and section == "must_not_appear":
            item["forbidden_patterns"] = list(patterns)
        groups[section].append(item)
    if not any(groups.values()):
        return None

    payload = {name: entries for name, entries in groups.items() if entries}
    return {
        "role": "system",
        "content": (
            "The following accepted Story Bible entries are authoritative for continuity.\n"
            "world_facts are true of this world; keep every scene consistent with them.\n"
            "character_facts are established about those characters.\n"
            "must_not_appear are things not to write; they are not facts about the world.\n"
            "Apply each entry only as far as it is written. Do not generalise an entry "
            "to subjects, senses, or situations it does not name.\n"
            "[MARGINALIA_ACCEPTED_CANON_V1]\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n[/MARGINALIA_ACCEPTED_CANON_V1]"
        ),
    }


def fiction_fixed_context_messages(
    *,
    project_config: CreativeProjectConfig,
    governor_dir: Path | str,
) -> list[dict[str, str]]:
    """The fiction system blocks, in the order generation sends them.

    Callers supply already-resolved state so this stays pure rendering: the
    request path keeps its cached stores, and operator tooling can build the same
    blocks without importing the web application.
    """
    messages: list[dict[str, str]] = []
    project_context = render_project_context(project_config)
    if project_context:
        messages.append(project_context)
    canon_context = accepted_canon_message(governor_dir)
    if canon_context:
        messages.append(canon_context)
    return messages
