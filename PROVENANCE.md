# Provenance

## Marginalia extraction

Marginalia was established on 2026-08-13 from the pre-Desk governed-chat
implementation in the `gov-webui` / Governor WebUI repository at commit
`b0c99e417363f216fd53490a31a8e6f5f485a92b`. That donor is a behavioral and
historical source, not the ongoing product architecture. Marginalia's first
implementation slice separated provider ownership, context-scoped pending
state, and receipt authority behind a narrow Agent Governor boundary.

The donor commit predates the Phosphor Desk/control-plane pivot. Marginalia has
no Constellation runtime dependency.

This project is human-directed and AI-assisted. Final design authority,
acceptance criteria, and editorial control rest with the human author.
AI contributions were material and are categorized below by function.

## Human authorship

The author defined the project direction, requirements, and design intent.
AI systems contributed proposals, drafts, implementation, and critique under
author supervision; they did not independently determine project goals or
deployment decisions. The author reviewed, revised, or rejected AI-generated
output throughout development.

## AI-assisted collaboration

### Architectural design, naming, and spec work

Lead collaboration: ChatGPT (OpenAI). Heavy involvement in mode design,
capture loop semantics, builder workflow architecture, naming taxonomy
(Agent Governor / Guvnah / Phosphor), style policy spec, and "what not
to build" decisions.

### Implementation, tests, and integration

Lead collaboration: Claude (Anthropic) via Claude Code. Heavy contributions
to source code, test suites, adapter wiring, artifact engine, style policy
module, receipt surface, effective config panel, and UI implementation.

### Governor-in-the-loop development

Agent Governor was used during Phosphor's own development: constraint
injection, receipt emission, and continuity enforcement were active during
implementation sessions. Included because it is unusual and directly
relevant to the project's thesis.

## Provenance basis and limits

This document is a functional attribution record based on commit history,
co-author trailers (where present), project notes, and documented working
sessions. It is not a complete forensic account of all contributions.

Some AI contributions (especially design critique, rejected alternatives,
and footguns avoided) may not appear in repository artifacts or commit
metadata.

Model names/tools are recorded at the platform level (e.g., ChatGPT,
Claude Code); exact model versions may vary across sessions and are not
exhaustively reconstructed here.

## What this document does not claim

- No exact proportional attribution. Contributions are categorized by
  function, not quantified by token count or lines of code.
- Design and implementation were not cleanly sequential. Architecture
  informed code, code revealed design gaps, and the feedback loop was
  continuous.
- "Footguns avoided" and "ideas that didn't ship" are real contributions
  that leave no artifact. This document cannot fully account for them.

---

This document reflects the project state as of 2026-02-24 and may be revised.
