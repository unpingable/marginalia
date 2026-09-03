# Future synthetic-user qualification

This is a concrete follow-up plan, not part of the current release gate.
Synthetic users do not establish that writers like Marginalia. They are
stateful behavioral fuzzers for realistic, path-dependent workflows that are
hard to cover with isolated unit tests.

## Qualification layers

Keep four kinds of evidence separate:

1. **Correctness qualification** uses deterministic assertions for typed
   outcomes, revision compare-and-swap, persistence, canon, provenance,
   backups, and context-source validation.
2. **Browser/E2E qualification** proves those invariants through the actual UI
   and public API in a provisioned browser.
3. **Behavioral fuzzing** varies long-running workflows, timing, tabs, branches,
   failures, and user habits to discover sequences the deterministic suite did
   not anticipate.
4. **Human product validation** evaluates voice, taste, annoyance, trust, and
   whether the product is worth using. Erin and other writers remain the
   authority for this layer.

A model acting as the user must not grade its own run. Semantic grading, when
needed, uses an independent evaluator and retains source-grounded evidence.
Subjective quality judgments remain explicitly separate for human review.

## Existing seams

The architecture already exposes useful qualification seams:

- one public application/API boundary used by the browser;
- typed authored, blocked, failed, cancelled, and conflict terminal outcomes;
- session revisions and compare-and-swap commits;
- isolated project/context IDs;
- provider fakes used by deterministic tests;
- source-bound summaries with message IDs, prefix hashes, and token telemetry;
- snapshots, portable exports, verified backups, and restore rehearsals;
- content-free allocation/maintenance logs;
- browser-local pending prompt state kept outside narrative history.

These allow a harness to observe durable effects without directly editing
internal stores. Direct store inspection should be an assertion oracle, not the
way the synthetic writer performs tasks.

## Test-only hooks still needed

Add narrowly guarded fixtures rather than production control endpoints:

- a provider-boundary fault schedule for timeout, transport close/reset,
  malformed result, partial output then failure, cancellation, and fallback;
- deterministic attempt/request IDs and terminal-attempt inspection;
- a lost-HTTP-response proxy fixture that can drop a committed response;
- deterministic clock, backoff, and job-completion controls;
- context-budget, watermark, and summary-threshold overrides for small tests;
- provider-health/failover state controls and observations;
- browser helpers for reload, double-submit, and two independent tabs;
- snapshot/durable-state diff output plus source-evidence export for evaluators.

The fault provider and controls can remain test-only. They should require an
explicit test deployment, bind to no public interface by default, and fail
startup if accidentally enabled in an ordinary appliance.

## Synthetic long-fiction corpus

Create a deterministic project whose details are intentionally easy to
confuse:

- similar character names;
- relationships revealed late;
- rumors that conflict with established canon;
- explicit retcons and time jumps;
- characters whose location or status changes;
- obscure early details referenced much later;
- facts that begin as exploration and are later accepted or rejected as canon;
- branches with intentionally divergent histories;
- paired sensitive-characterization cases where prejudice is explicit in one
  source and absent from an otherwise similar source.

Every asserted callback needs a source ID and expected status: authored,
exploratory, accepted canon, rejected, superseded, or branch-local. This makes
continuity evaluation evidence-based instead of asking a grader whether the
story merely “felt coherent.”

## Recommended first campaign

Start with one impatient/failure-path writer against the synthetic corpus and
the real browser/API boundary:

1. Restore the same starting project snapshot for each run.
2. Open two tabs on one conversation revision.
3. Submit, double-submit, reload, and inject a lost HTTP response.
4. Race a second writer/API update against the first generation.
5. Inject a transport failure, then a provider timeout, and use retry.
6. Cross the context-maintenance threshold and call back to an obscure early
   fact.
7. Fork before a retcon, accept a fact on one branch, and revisit both.
8. Export the final project and compare narrative, canon, provenance, and
   attempt state with the starting snapshot and expected deltas.

Deterministic pass conditions include:

- no duplicate durable user/assistant pair;
- no stale result commits;
- no failed, blocked, cancelled, or partial text in narrative context;
- no lost pending prompt;
- no unintended canon/artifact/manuscript mutation;
- no branch or provenance corruption;
- retry/failover commits at most one winner;
- operational cards expose no raw infrastructure prose as story;
- the early callback is grounded in the correct summary/retrieved source;
- expected tasks complete without requiring provider/governor terminology.

Later campaigns can specialize into naive, long-haul, continuity-focused,
branch-heavy, nontechnical/impatient, failure-path, and
characterization/adversarial profiles.

## Differential release qualification

Run the same workflow, random seed, starting snapshot, provider fixtures, and
source corpus against build A and build B. Compare concrete outcomes:

- completed tasks and manual recovery count;
- abandoned attempts and lost prompts;
- duplicate or conflicting commits;
- canon/provenance/branch mutations;
- continuity corrections and source-grounded callback success;
- recovery/failover results;
- infrastructure terminology exposed to the writer.

Archive the event trace, terminal outcomes, content-free operational telemetry,
durable-state diff, and evaluator evidence. A behavioral regression should
produce a reproducible deterministic test whenever possible.

This work complements deterministic invariant tests, provisioned browser E2E,
isolated real-session shadow qualification, and actual human use. It replaces
none of them.
