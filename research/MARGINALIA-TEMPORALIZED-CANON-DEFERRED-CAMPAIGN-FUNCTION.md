# Marginalia — TEMPORALIZED-CANON (deferred campaign function)

Date recorded: 2026-09-06

Status: **DEFERRED CAMPAIGN FUNCTION / NOT ACTIVE**

Recording this opens no campaign, changes no module, and enables no temporal
behaviour for any existing project. It preserves a design exposed by the canon
authority work so it can be picked up without reconstructing the conversation
that produced it. Marginalia's campaign convention is a `research/` design
document plus a `NEXT_WORK.md` backlog entry; no executable stub is required and
none was added.

Name is provisional. Prefer existing Marginalia terminology at activation if a
better fit exists — the codebase already says *canon*, *anchor*, *world fact*,
*validity*, and *supersession*, and "temporalized canon" should not survive if
one of those extends naturally.

---

## 1. Motivation

The canon authority and open-ontology work closed the question of *who may
change canon*. It left a chronology dimension that Marginalia currently handles
only through prose and model interpretation.

Fiction carries at least four distinct orderings, and Marginalia flattens them:

| ordering | what it tracks |
| --- | --- |
| **world validity** | when a fact is actually true in-universe |
| **character epistemic state** | when a character knows, believes, remembers, or can perceive something |
| **narrative / revelation order** | when the reader or viewpoint is shown it |
| **canon-recording order** | when the author entered, revised, or retired it in Marginalia |

> Narrative order, world validity, character knowledge, and canon-recording
> order are distinct unless authoritative evidence explicitly relates them.

Three consequences that are currently indistinguishable from defects:

- A fact revealed in chapter 30 may have been true in-world since 1987. Neither
  a contradiction nor necessarily a supersession.
- `robot X is human-form during WAR` beside `robot X is non-human-form after
  BAN` is a temporal transition, not a conflict.
- `FACT_X is canon` beside `Nouran does not know FACT_X yet` is neither missing
  canon nor inconsistent characterisation.

Marginalia already enforces the coarse version of this: registration order
authorizes nothing, and a `contradictory_state` candidate needs canon to
explicitly retire the anchor it targets (`canon_scope.supersession_statements`).
That prevents a bad repair from promoting. It does not let the product
*classify* what it is looking at, which is what this function is for.

---

## 2. Feature shape

Temporalization must be **optional and progressively structured**. Fiction
routinely has only partial chronology — `before WAR_END`, `after JINWOO_DEATH`,
`during COLLEGE`, `between EVENT_A and EVENT_B` — so do not design around a
mandatory total order or calendar timestamps.

A conceptual ladder, naming TBD:

```
temporal_model:
  none            # today's behaviour, and the default forever
  ordered_events  # named anchors and partial order
  calendar        # only where the project actually has dates
```

Prefer temporal anchors, intervals, and partial ordering over timestamps.

### 2.1 Claim-level state, not object-level

Do not attach one mutable "time" field to a character or world object. Qualify
individual claims, so identity persists while properties vary:

```
subject: Halo
predicate: rank
value: Sergeant
world_validity:
  from: PROMOTION
  to: DEMOTION
```

This is what separates character development from inconsistency. It should carry
rank and status changes, relationships changing, injury/death/transformation,
knowledge acquisition, changing beliefs and capabilities, temporary conditions,
world laws taking effect, and facts true only within a bounded interval.

### 2.2 Character epistemic state

Likely the highest-value part, and the easiest to over-build. Marginalia should
eventually represent, *when explicitly useful*:

```
FACT_X is true in canon
  Margie knows FACT_X    after EVENT_A
  Keisha suspects FACT_X after EVENT_B
  Nouran does not know FACT_X at the current story position
```

Investigate the **minimum** representation that separates world truth, character
knowledge, character belief, character perception, and reader/revelation state.
Add a distinction only where it materially improves writing correctness. This is
not an epistemic logic engine.

---

## 3. Contradiction classification

The payoff. A temporal axis should let Marginalia distinguish:

| class | shape |
| --- | --- |
| **actual contradiction** | same subject, scope, world-state and time; incompatible authoritative claims |
| **temporal transition** | both valid, different world intervals |
| **later revelation** | new canon describes an earlier world state |
| **refinement** | later information enriches an incomplete model without invalidating it |
| **supersession / retcon** | later authoritative canon replaces an earlier assertion |
| **observer disagreement** | characters hold incompatible beliefs; the world state is not contradictory |
| **misclassification corrected later** | an entity believed to be category A is revealed as category B |

The last one matters for delayed reveals and mystery structure, and is where
this function meets the open-ontology work.

---

## 4. Crosswalk: open ontology

Chapter 3 states `Robots cannot perceive ghosts.` Chapter 30 reveals that a
supposed robot was an uploaded human. The correct reading is usually:

- the generic rule stands;
- the *entity's classification* was incomplete or wrong;
- later revelation changed knowledge about an earlier state.

A later classification change must not be read as retroactive contradiction. See
`NEXT_WORK.md` backlog item 6 and the skunkworks deferred item §8 it names.

---

## 5. Crosswalk: authority and provenance

Everything shipped in the authority work remains in force. A model-derived
temporal relation is a model-derived proposition:

```
"this probably happened before the war"
```

is not an author-authenticated temporal relation. Temporal reasoning may produce
diagnostics or candidate relations; consequential canon mutation still needs an
admissible warrant. If activation introduces temporal warrants, they belong in
the existing `MUTATION_ADMISSIBLE_WARRANTS` / `DIAGNOSTIC_ONLY_WARRANTS` split
rather than a parallel mechanism.

---

## 6. Default behaviour

Existing projects stay valid with no temporal metadata. The safe default is
equivalent to `validity: unspecified / timeless`. Authors are never forced to
temporalize material that does not need it, and **no bulk migration of existing
canon happens because this function exists**.

---

## 7. Investigation at activation

1. Inventory how Marginalia represents canon facts, characters, world state,
   source/revelation position, revisions and supersession, author resolutions,
   and provenance.
2. Identify where chronology is currently explicit, inferred from prose,
   flattened away, or conflated with source ordering.
3. Crosswalk against constellation work **before inventing semantics**:
   temporal validity and freshness, supersession and reassessment,
   observer-relative evidence, admissible evidence cuts, state continuity,
   identity through change, provenance, authority, warranted reliance,
   refinement versus contradiction, and any existing temporal or partial-order
   results in the Lean/skunkworks corpus. Do not reinvent temporal logic inside
   Marginalia if an existing abstraction can be reused or adapted.
4. Propose the minimum viable representation.
5. Consider partial orders and event anchors explicitly; do not assume calendars.
6. Decide what belongs in canonical persisted state, optional metadata,
   derived/indexed state, and diagnostics only.
7. Design backward-compatible migration and serialization.
8. Add adversarial fixtures before enabling anything for production authors.

---

## 8. Required regression fixtures

1. Fact revealed late but true much earlier in-world.
2. Same property legitimately changes across two intervals.
3. Character knows a canon fact only after a particular event.
4. Character speaks before acquiring that knowledge and is correctly flagged.
5. Two characters disagree without canon contradiction.
6. Later subtype/classification reveal explains earlier behaviour without retcon.
7. Genuine same-time incompatible world facts remain a contradiction.
8. Explicit later retcon/supersession distinguished from revelation.
9. A story with no chronology metadata behaves exactly as before.
10. A model-inferred temporal relation cannot independently authorize mutation.
11. Flashback / nonlinear narration where chapter order differs from world order.
12. Partially ordered events where exact dates are unknown.

Fixture 9 is the acceptance gate for existing authors; fixture 10 is the
acceptance gate against the authority boundary.

---

## 9. Non-goals

Not a temporal logic research project. Not mandatory calendar management. Not a
requirement that every sentence carry a timestamp. Not a general epistemic-logic
engine. Not a replacement for author judgement. Not automatic retcon detection
from model inference alone. Not a broad schema rewrite unrelated to actual
temporal needs.

The product feature is narrow:

> Marginalia may optionally preserve enough temporal structure to avoid treating
> change, delayed revelation, unequal knowledge, and nonlinear narration as
> ordinary contradiction.

---

## 10. Dependencies

- **Canon authority boundary** — shipped. Warrants, `relied_on`, sticky
  resolutions. Temporal warrants must extend it, not bypass it.
- **Open ontology / non-exhaustion** — partially shipped
  (`canon_scope.closure_statements`, `supersession_statements`); the
  classification half is deferred. §4 above depends on that half.
- **Constellation temporal/partial-order results** — must be surveyed before
  any semantics are defined here.
- No dependency on the context-budget or provider work.

## 11. Expected artifacts

1. Inventory and crosswalk of current chronology handling (§7.1–7.3).
2. Minimum viable representation proposal, with the persisted /
   optional / derived / diagnostic split decided (§7.4, §7.6).
3. Backward-compatible serialization and migration design (§7.7).
4. The twelve fixtures of §8, adversarial, landing before any author-visible
   behaviour.
5. A classification table implementation covering §3, or an explicit decision
   that some classes stay diagnostic-only.

## 12. Activation criteria

Any of:

- an author hits a real chronology misclassification in production — change,
  delayed revelation, or nonlinear narration reported as a defect;
- the open-ontology classification half is picked up, since §4 is a shared
  boundary and doing them separately would duplicate the crosswalk;
- constellation temporal/partial-order work reaches a state where Marginalia is
  the natural testbed;
- an author explicitly asks for character-knowledge tracking, which is §2.2 and
  the most valuable slice on its own.

Absent a trigger this stays deferred and must not preempt the priority backlog.

---

*Recorded 2026-09-06. Documentation only: nothing opened, nothing enabled, no
canon modified.*
