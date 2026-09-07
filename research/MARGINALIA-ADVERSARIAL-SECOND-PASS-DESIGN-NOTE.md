# Marginalia — adversarial second pass (design note)

Date recorded: 2026-09-06

Status: **DESIGN NOTE / CAMPAIGN FUNCTION WITH RISK-TRIGGERED ACTIVATION**

Not a requirement that Marginalia run two frontier models to write a sentence.
A second model is spent only where a transformation can permanently record or
constrain canon, and stays off everywhere else.

Working names: *dual-model adjudication*, *adversarial second pass*. Prefer
existing Marginalia vocabulary at pickup if something fits.

---

## 1. Why now

Marginalia has crossed the threshold where a second model buys real reliability.
The canon authority boundary shipped a policy that decides *whether* a finding
may change canon; it cannot judge whether the finding is any good. Nothing
currently reviews a model-produced interpretation before it becomes a durable
proposal, and the recent incident showed what that costs: a debugging pass
produced several confident readings of an author's world rules, each of which
looked like a defect and none of which was.

The boundary caught them — none could authorize a mutation — but only because
the author kept correcting them. That is the wrong reviewer.

## 2. The pattern

```
primary model
  -> produces interpretation / summary / repair candidate

second model
  -> adversarially checks:
       source fidelity
       authority provenance
       scope narrowing
       temporal assumptions
       obligation-locus drift
       unsupported contradiction claims

system
  -> compares results
  -> disagreement becomes diagnostic evidence
  -> neither model gets authority merely by winning the argument
```

The last line is the whole design.

> **Two models agreeing is not canon. Two models disagreeing is not a defect.**

Both outputs are evidence *about the transformation*, not authority over the
author. A second pass must not become a majority vote that launders two model
opinions into one authoritative claim — that would reintroduce exactly the
failure the authority boundary exists to prevent, one layer up. The challenger's
findings are themselves model-derived and carry diagnostic-only warrants unless
they point at something source-provable.

## 3. Asymmetric roles

Not "model A and model B both solve it". The roles differ:

**Producer.** *Here is my result, and the warrants for each part of it.*

**Challenger.** *Find any unsupported semantic strengthening, authority
laundering, missing evidence, or lossy transformation. Do not propose
improvements unless you can point at a defect.*

That last clause is load-bearing and comes directly from the incident. An
unconstrained second model does what the first one did: invents a clever
alternate reading and calls it debugging. The challenger is not asked whether it
would have written the canon differently. It is asked whether the producer's
output is *supported*.

Symmetric review would double the interpretation surface rather than constrain
it.

## 4. Typed outcomes, not a score

The interesting result is a disposition, and the vocabulary already exists in
`canon_review_store.py` — the two warrant sets map onto this directly:

```
AGREE / independently supported
DISAGREE / semantic interpretation      -> diagnostic-only warrant
DISAGREE / source-fidelity defect       -> mutation-admissible warrant
INSUFFICIENT WARRANT
NEEDS AUTHOR RESOLUTION
```

`DISAGREE / semantic interpretation` is the common case and must stay cheap: it
records a disagreement without blocking anything and without accusing the
author. `NEEDS AUTHOR RESOLUTION` routes to the sticky-resolution mechanism, so
an answered question stays answered.

## 5. Where to spend the inference

Where a transformation can permanently record or later constrain canon:

- canon promotion and repair proposals;
- contradiction versus refinement versus supersession classification;
- author-statement extraction;
- context summarization that will later constrain generation;
- anything that mutates or permanently records canon;
- submitted-to-agent incidents, where one model diagnoses and another
  hostile-reads the diagnosis **before anything is promoted**.

Not:

- ordinary prose generation;
- simple retrieval;
- mechanical CRUD;
- every chat turn;
- obviously source-preserving operations.

Context summarization deserves a note. It is the quietest entry on the list and
possibly the most valuable: a summary is not shown to the author, is trusted by
every later turn, and silently constrains generation for the rest of the
project. A defect there is durable and invisible.

## 6. Exhaust the cheap checks first

A second model is the expensive instrument. Marginalia already has non-model
gates that cost nothing and should run first — schema validation on summary
output, the requirement that every fact cite source message IDs, counter
identity, prefix hashing, the anaphora guard, closure and supersession
detection. Anything a deterministic check can catch should never reach a
challenger.

The second pass is for what deterministic checks structurally cannot see:
whether a claim is *supported by* its source.

## 7. Different families, not two instances

For higher-risk cases prefer a challenger from a **different model family or
provider**. Two instances of one model share blind spots, and the failure being
guarded against is precisely a plausible-sounding reading — the thing a model
agrees with itself about most readily.

Claude plus Codex or Kimi is more useful than Claude arguing with Claude. The
provider catalog already supports this: it holds multiple families, and
`purpose: "context-maintenance"` already demonstrates a second configured model
role with its own identity. A `purpose: "challenger"` entry would follow the
same shape. Model diversity is a configuration property, not new architecture.

## 8. Risk-triggered activation

Off by default. Candidate triggers, to be settled at pickup:

- a proposal carrying a mutation-admissible warrant against existing canon;
- a summary that will cover more than some threshold of session history;
- any submitted-to-agent incident diagnosis;
- author opt-in for a project;
- a repeat proposal on evidence the author previously resolved.

The cost model matters — this is a writing appliance with a real budget, and the
local model is free. A local challenger against a hosted producer is a plausible
default, and Orion has already served in a comparable role.

## 9. Interaction with other recorded work

- **Canon authority boundary** (shipped) — supplies the warrant vocabulary of
  §4. The challenger is bound by it exactly as the producer is.
- **Open ontology / non-exhaustion** — "scope narrowing" in §2 is that check;
  `canon_scope` supplies the deterministic part of it.
- **TEMPORALIZED-CANON** (backlog 7) — "temporal assumptions" in §2. The
  contradiction/refinement/supersession classification in §5 is that item's §3
  table, and a challenger is a plausible way to implement it without formal
  machinery.
- **Obligation-locus preservation** (skunkworks §9) — "obligation-locus drift"
  in §2, and the check most likely to catch an agent projecting its own rules
  onto the author.
- **SUBMIT-TO-AGENT** (backlog 8) — §5's last entry. Cheap adversarial review
  attached exactly where the value is highest, and the two campaigns are
  cheaper together than separately.

## 10. Non-goals

Not a model debate club. Not majority voting. Not consensus as a truth
criterion. Not a challenger empowered to rewrite the producer's output. Not
always-on. Not a replacement for the authority boundary, the deterministic
gates, or author judgement.

## 11. Open questions

- Does a challenger disagreement need its own warrant, or is it an ordinary
  diagnostic-warranted review item? The second is cheaper and probably right.
- Does the author ever see a `DISAGREE / semantic interpretation`, or is it
  admin-only until it recurs? Showing every one would be noise; hiding all of
  them wastes the signal.
- How is challenger cost bounded on a long session — per proposal, per project,
  per day?
- Does the challenger see the producer's warrants, or only its output? Seeing
  the warrants makes it a better auditor and a worse independent check.

That last one is the real design question and should not be answered casually.

---

*Recorded 2026-09-06. Design note only: nothing implemented, no second model
configured, no activation trigger enabled.*
