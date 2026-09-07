# Marginalia — SUBMIT-TO-AGENT (operator-originated diagnostic handoff)

Date recorded: 2026-09-06

Status: **CAPTURED AS CAMPAIGN / IMPLEMENT WHEN APPROPRIATELY SCHEDULED**

This is not deferred research. It is a product campaign waiting for a slot. The
constellation crosswalk of the same pattern *is* deferred and is recorded
separately in
`skunkworks/research/DEFERRED-OPERATOR-ORIGINATED-REPAIR-EVIDENCE-2026-09-06.md`.
Keep the two dispositions distinct: scheduling this campaign opens nothing in
the constellation, and the crosswalk opens nothing here.

Name is provisional. `SUBMIT-TO-AGENT` describes the button, not the mechanism;
prefer a name from existing Marginalia vocabulary at scheduling if one fits.

---

## 1. Motivating incident

During a live debugging session an agent tailed container logs to catch problems
while the author kept writing. That worked for the mechanical half: two 502s
were caught with their exact timings, and the timings turned out to *be* the
diagnosis — both requests died precisely on a configured bound.

It did not work for the semantic half. Every semantic defect in that same
session reached the agent because a human relayed it in prose:

- canon rules being applied to the wrong entities;
- an agent's own operating constraint being projected onto the author;
- a derived reading reported to the author as a defect in her own material.

None of those produced a log line. Each was a mechanically successful request.

Two structural weaknesses showed up plainly. The log tail died silently when the
container was recreated, so the watch outlived its usefulness without saying so.
And even while healthy it could only answer *what did the system do* — never
*which of these outputs did the author consider wrong*.

## 2. What the operator knows that telemetry does not

Mechanical failures are well served by existing paths: exceptions, failed
requests, malformed data, timeouts, wrong revisions, obvious pipeline faults.

Semantic failures are not, and they are the ones that matter to a writer:

- "this misunderstood what I meant";
- "this is applying the chatbot's rule to me";
- "this inference is wrong";
- "this technically succeeded but did the wrong thing";
- "this violates my canon and nothing failed".

The author knows **which occurrence is wrong**, and usually knows something
about the nature of the failure that cannot be reconstructed from logs. That is
privileged evidence, and there is currently no way to contribute it.

The intended flow:

```
author observes bad behaviour
    -> explicitly submits that occurrence
    -> Marginalia freezes the relevant evidence boundary
    -> the configured repair agent receives a bounded incident
    -> agent diagnoses, asks for clarification, or proposes a repair
    -> normal authority and promotion gates remain in force
```

## 3. Product surface

Deliberately small:

```
[ Submit to agent ]

What was wrong here?
[ free-text note ]
```

The author is the **witness, not L1 support**. Do not require them to diagnose
Marginalia's internal failure class. Optional lightweight categories may help
later — *misunderstood me*, *wrong canon*, *ignored instruction*, *unexpected
behaviour*, *bad output*, *other* — but exact occurrence identity and free-text
testimony matter far more than taxonomy, and taxonomy must never be mandatory.

## 4. Freeze the occurrence

The critical property. A submission refers to the historical occurrence the
author judged defective, not to whatever the project looks like later.

> The repair agent investigates the occurrence the operator saw, not whatever
> Marginalia happens to look like five minutes later.

The envelope binds enough identity to reconstruct that boundary. Field names
below are illustrative; **reuse existing identities rather than minting new
ones** — receipts, revision IDs, content hashes, attempt IDs, provenance records:

```
incident_id
project / session identity
turn / generation / request identity
submitted output
author testimony
canon revision
context revision (summary coverage, prefix hash)
prompt / build revision
model and provider identity
execution / attempt identity
timestamps
trace / log references
authority and provenance state
```

Marginalia already has most of this. Session revisions, `observed_revision` and
`prefix_sha256` on summaries, receipt IDs, configured provider/model identity,
and the deployment build SHA all exist and are already durable. The campaign is
mostly *binding* them to an incident, not inventing them.

## 5. Testimony is evidence, not authorization

The submission itself is evidence:

```
operator asserts: this occurrence is defective in some relevant way
```

That is meaningful precisely because the author can observe semantic
correctness that execution telemetry cannot. But:

> Reporting a defect is not authorization to repair it.

A submission does not authorize canon mutation, prompt changes, state changes,
deployment, interpretation changes, or authority rebinding. Diagnosis, proposal,
approval, and promotion gates all remain in force. This must be explicit in the
implementation and visible in the UI, not merely true by omission.

The existing canon-authority boundary already expresses the shape of this: a
finding may be recorded without being admissible as a mutation warrant.

## 6. Repair-agent endpoint

The receiving side is an **abstract diagnostic endpoint**, not "Claude". Today
that may be Claude Code; later Codex, Kimi, a local agent, a Maude-mediated
session, or another supervised repair process. The author-facing workflow must
not change when the endpoint does — the same requirement the provider catalog
already satisfies for writing models, and probably the same shape of solution.

The agent should be able to return dispositions equivalent to:

- diagnosed system defect
- likely user/model interpretation mismatch
- needs authoritative clarification
- mechanically reproducible failure
- unsupported report / cannot reproduce
- repair proposed
- already fixed / covered by an existing repair
- no defect found

Prefer existing Marginalia outcome vocabulary where it fits; these are typed
outcomes, and the codebase already has a discipline for those.

## 7. Clarification loop

The highest-value path, and the one that keeps the author out of an external
debugging conversation:

```
operator report
    -> agent investigation
    -> agent requires author clarification
    -> question returned through Marginalia
    -> author answers
    -> answer becomes attached authoritative evidence
    -> diagnosis continues
```

The answer retains provenance and attaches to the incident it resolves. This
connects directly to sticky author resolutions: an answer given here is exactly
the kind of disposition that must not be re-litigated by a later pass deriving
the same question from the same evidence.

## 8. Reply surface

Compact, for a writer, not a developer console:

```
Report #184
Status: diagnosed
Cause: scope was narrowed from a generic category to observed instances.
Disposition: system defect
```

```
Report #184
Status: needs clarification
Did you intend "robots" to include uploaded-human constructs?
```

Low-level traces stay available for debugging and admin use, behind that
surface. Ordinary authors should never be shown a live trace feed.

## 9. A separate channel from telemetry

Distinct evidence path from execution logs.

| path | answers |
| --- | --- |
| mechanical telemetry | what did the system do? |
| operator report | which occurrence does the author consider semantically wrong, and what did they observe that the machine could not? |

Do not overload ordinary logging with semantic testimony, and do not require the
repair agent to tail everything and infer which behaviour upset the author. The
motivating incident is exactly the failure of that inference.

## 10. Security and authority boundary

Inspect explicitly:

- submitting project context to a different provider or agent;
- bounded context selection for the envelope — it must not become an
  unbounded export;
- project isolation and accidental cross-project delivery;
- stale agent sessions;
- **prompt injection from submitted model output** — the submitted occurrence is
  generated text and is data;
- **author testimony mistaken for a command to the repair agent** — the note is
  evidence, not an instruction;
- **repair-agent findings mistaken for author authority** — a diagnosis is not a
  resolution.

The last three are the same class the canon-authority work addressed and should
reuse that machinery rather than restating it.

### 10.1 Trust profile — an explicit deployment assumption

The constellation item's security clause
(`DEFERRED-OPERATOR-ORIGINATED-REPAIR-EVIDENCE-2026-09-06.md` §4) covers
non-escalatory handoff, disclosure control, and incident-scope confinement. Most
of it does not bind this deployment, and saying why is the point — the risk is
not that Marginalia is under-defended, it is that a permissive choice made for
one trusted author silently becomes the architecture for everyone.

> **Initial deployment assumes trusted operator/QA submitters; any broader
> multi-user exposure must explicitly revisit incident visibility, disclosure,
> agent privilege, and submitter authority.**

The current author is a trusted operator and design partner, not a tenant. In
that profile the threat model is *"the repair agent must not mutate canon
outside the existing authority gates"* — which the shipped canon boundary
already enforces — and not *"a hostile submitter attempts privilege
escalation"*. A rich envelope, a direct diagnostic handoff, and a full
author-resolution reply path are all reasonable here.

This does **not** generalise. A public or multi-tenant deployment needs the
narrower envelope, stronger project isolation, and possibly no privileged
diagnostic backend at all. High-consequence systems need that clause in full, where
the repair channel is itself part of the security boundary.

One cheap habit worth keeping regardless, because it costs nothing now and is
irritating to retrofit: **the system builds the envelope from the occurrence the
UI is displaying**, rather than accepting hand-crafted identifiers. The author
submits *this response*, not `session_id=<arbitrary>`. That keeps §4.5
satisfied by construction without any of that clause's other machinery.

### 10.2 Why these reports are worth more than feedback

The submitter here is performing in-situ adversarial QA *with author authority*,
which is a different thing from an end-user complaint. Her reports can
legitimately become regression evidence, authority resolutions under the sticky-
resolution mechanism, and product design input — three uses that ordinary
end-user feedback should **not** automatically acquire.

The incident store should therefore be able to carry a report forward into a
fixture or a resolution without a separate transcription step. That capability
is profile-dependent, not universal, and belongs behind the same assumption
recorded in §10.1.

## 11. Acceptance cases

1. Submitting a specific generated response attaches the correct historical
   revisions and identities.
2. Canon changes after submission; the incident still reconstructs the original
   occurrence.
3. The report reaches the configured repair endpoint.
4. The endpoint can diagnose without acquiring mutation authority.
5. The agent can request author clarification.
6. Clarification attaches to the incident with appropriate authority and
   provenance.
7. A report can yield a proposed repair but never a silent deployment.
8. Mechanical errors continue through ordinary telemetry and do not depend on
   this path.
9. Semantic reports exist even when the underlying request succeeded
   mechanically.
10. Switching repair-agent providers does not change the author-facing workflow.
11. Duplicate submissions are recognised or idempotently handled.
12. Incident history remains reconstructable after later project changes.

Cases 2 and 12 are the freeze property. Cases 4 and 7 are the authority
boundary. Case 9 is the reason the campaign exists.

## 12. Non-goals

Not generalized support ticketing. Not automatic semantic bug fixing. Not
automatic deployment from reports. Not a conversational issue tracker. Not
universal agent messaging infrastructure. Not a replacement for logging. Not a
new provenance system. Not a requirement that the author classify root cause.
Not real-time surveillance of every interaction.

Build the smallest coherent operator-to-repair-agent handoff that materially
improves debugging.

## 13. Dependencies

- **Canon authority boundary** — shipped. Supplies the evidence-versus-warrant
  distinction §5 and §10 depend on, and the sticky-resolution behaviour §7
  extends.
- **Typed generation outcomes and receipts** — shipped. Supplies most of the
  envelope in §4 of this document.
- **Provider catalog abstraction** — shipped. The model for §6's endpoint
  indirection.
- No dependency on temporalized canon or the open-ontology classification work,
  though §7 clarifications would become richer with the former.

## 14. Expected artifacts

1. Incident envelope schema, built from existing identities, with an explicit
   list of what is bound versus copied.
2. A durable incident store with the same atomic-write discipline as the other
   project-scoped stores.
3. Endpoint abstraction plus one concrete adapter.
4. Author-facing submit control and status surface.
5. Clarification round-trip with provenance retained.
6. The twelve acceptance cases of §11.
7. A security review covering §10 before any endpoint is enabled by default.

## 15. Scheduling notes

Reasonable to schedule once the priority backlog's attempt-identity item lands,
since durable attempt IDs are the cleanest anchor for §4 occurrence identity and
building the envelope first would duplicate them. Not blocked on it — the
session revision and receipt ID are sufficient for a first version — but
sequencing the other way is cheaper.

The clarification loop (§7) is the most valuable slice and could ship alone if
the campaign needs to be cut down.

---

*Recorded 2026-09-06. Captured as a campaign; nothing implemented, no endpoint
enabled, no author-facing surface added.*
