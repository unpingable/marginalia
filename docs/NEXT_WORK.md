# Next work

## Full ag-ng migration candidate — 2026-09-08

Agent Governor classic is no longer a Marginalia runtime dependency. Candidate
`718ac4549a99b24bd935432ecf44eda45acac9f3` composes ag-ng authorization,
Docket attempt custody, credential-isolated `ag-providerd`, and Marginalia's
revision/canon-checked acceptance boundary. The qualified image and deployment
preconditions are recorded in `docs/ag-ng-migration/`. The 839-test baseline is
semantically crosswalked in `TEST-COVERAGE-CROSSWALK.md`; the exact-companion
witness is a mandatory post-image CI step rather than an unexplained
release-suite skip.

Production is intentionally unchanged. The remaining operational action is the
separately approved production cutover, beginning with OpenRouter credential
rotation and file-mounted NAS custody. After cutover, pause broad feature work
for Erin's real-use feedback and passive telemetry. The priority backlog below
remains the planning record; it is not an executable campaign.

## Planner/executor contract campaign — 2026-09-05

A fourth incident on session `78fc7d45675f4a21` produced three `context_maintenance`
failures with a fresh incident ID each and no underlying progress. The cause was
not a stale lock, crashed worker, corrupt state, or abandoned transaction:
nothing was stale and the persisted state was internally consistent. Generation
sized required coverage from the real fixed project context and the writer's
actual prompt and needed 82–84 messages; maintenance independently re-derived
the same quantity from an empty fixed-context list and the placeholder prompt
`"Continue the story."`, concluded 80 sufficed, and its "already sufficient"
check made it a silent no-op on every retry. Both components were internally
correct and collectively deadlocked.

A hostile audit of the surrounding pipeline found the same shape at three more
boundaries. Five bounded repairs followed, each with a regression test verified
to fail against the prior code, and two latent inputs were closed afterwards:

1. The maintenance watermark, a proactive heuristic measured on history alone,
   could return before the propagated coverage floor was read. A project whose
   fixed context and prompt exceeded the admission limit while history sat under
   the watermark wedged identically and permanently. The watermark is now
   consulted only when no observed requirement is present.
2. Required coverage is computed once, by admission, and travels on
   `ContextMaintenanceRequired` instead of being recomputed by the caller.
3. Fixed context construction moved to `gov_webui/fixed_context.py` so the
   request path and operator tooling cannot assemble different approximations,
   and `ContextOperations` sizes with the session's own model tokenizer.
4. Readiness became three-valued. `/health/ready` and the ops CLI previously
   reported ready from placeholder sizing while the runtime rejected the
   session, and `context-activate` used that verdict to authorise durable state.
5. Concurrent requirements join monotonically; terminal maintenance failures are
   no longer retried on the backoff schedule or reported to the writer as
   transient; and a shorter requirement prunes the checkpoint cache instead of
   discarding it.
6. Summaries and checkpoints record the token counter that produced them, so a
   counter change is detectable rather than silently redefining sufficiency.
7. A model may declare its context window, which narrows the effective budget for
   that request instead of applying one per-project constant to every model.

The reusable invariant is that the consumer computes its requirement and the
preparer satisfies it. Prefer `compute once → propagate exact requirement →
satisfy → verify` over `compute → independently recompute → hope both agree`.
Any future preparation step that can declare an operation admissible must
evaluate a context at least as demanding as the one the operation will execute
under.

Recovery was validated end to end on a disposable instance against a local
Ollama model: a session in the wedge shape returned one preparation outcome,
maintenance ran and satisfied the propagated requirement, and the retry authored
normally. Persistence was not implicated at any point — the checkpoint,
publication, and resume boundaries were audited and found sound, including a
real provider timeout that resumed correctly in three calls.

## Context liveness incidents resolved — 2026-09-04/05

Live build `70a6c9f9f4674c60d842c44e63e1617d3ebf6218` is healthy on image
`marginalia:context-alias-70a6c9f` (`sha256:cf447b10cb400a4370ab87678c4d714b505c996e95abc74f63feaf28ae826cc9`).
Operator maintenance mode is inactive, `/health/ready` reports context
preparation `ready`, and the affected session validates at 80/80 summary
coverage.

The first liveness incident involved session `78fc7d45675f4a21` in project
`fc3b21be00c3` at revision 18: 90 messages and 90,922 counted history tokens.
Its valid summary covered 51 messages while proactive lookahead required 62.
Interactive generation synchronously encountered remote Claude maintenance and
waited for its 240-second timeout before the writing model could run. The
interactive path no longer awaits remote maintenance; it returns typed
`context_maintenance` immediately and schedules resumable background work.

A second organic incident, `gen-f101733f5580`, occurred after six successful
turns at revision 24 / 102 messages. The summary covered 62 while proactive
lookahead required 80, but interactive admission used only the hard 32k
application budget and launched a 42,349-token predicted Codex request. Codex
timed out at 240 seconds. Interactive admission now retains the same 10,666-token
lookahead as the prebuilder, so the default provider-launch ceiling is about
37,334 predicted tokens. After recovery, a read-only real-compiler plan measured
35,147 predicted tokens with 80 summarized and 22 recent messages.

Claude summary maintenance now uses a typed internal model purpose, hidden and
rejected at writer-facing model APIs. Only that internal route receives Claude's
native `--json-schema` constraint; ordinary Claude writing remains unchanged.
A story-free 56-fact / 25,165-character dense reproduction completed in 62.5
seconds under a 120-second qualification bound and passed Marginalia's unchanged
fact, text, evidence, and source validation. One live constrained result cited
an out-of-scope evidence ID and was correctly rejected; the bounded retry then
completed. Validation was not weakened.

Changing the configured maintenance alias initially made legacy checkpoint work
appear incompatible. The rebuild was stopped, and only the derived `.work.json`
was restored from the verified backup. Checkpoint alias reuse is now permitted
only for catalog entries with identical protocol, provider, and upstream model,
with the existing prompt-version and exact source-prefix checks still required.
The recovered work was reused and the final summary is bound to the new internal
alias.

The final qualification is 682 passing tests plus Ruff, formatting, browser
startup smoke, exact Agent Governor contract qualification, image import smoke,
and an isolated governed Codex PASS with an authority receipt. The fresh backup
is `/backups/erin/marginalia-erin-20260905T000357579442Z.zip`, SHA-256
`1d0c383e704a4af8a63831b91917ba3b63770279c740e3441d77b23324ecda1d`;
it was independently verified and restore-tested with all 730 messages and 114
canon reviews. Preflight remained one workspace, two projects, seven sessions,
and 730 messages throughout final recovery. No failed prompt, error text, or
maintenance output entered narrative state.

Freeze this baseline long enough to collect real usage evidence before starting
another broad reliability campaign. Writer reports and content-free telemetry
should determine which item below moves first.

## Qualified baseline

The current invariant is:

> A generated passage can enter durable narrative state only as a validated
> authored outcome committed against the exact session state from which it was
> generated. Operational, blocked, cancelled, partial, and conflicting outcomes
> remain outside narrative state.

The deterministic suite covers the typed outcome boundary, revision
compare-and-swap, failure persistence, bounded context, source-linked summaries,
backups, restore rehearsal, and durable generation custody. Gate 3 qualified the
complete Marginalia suite (839 passed), the exact pinned ag-ng and Docket suites,
their cross-process witness, and the focused browser reliability flow. See
`docs/gate3/GATE3H-VALIDATION.md` for exact commits and evidence.

Context summaries have prefix semantics. `observed_revision` records provenance;
it is not a validity equality check. A summary covering messages 1–51 remains
valid after messages 52+ are appended when the covered prefix IDs and content
hash are unchanged. Editing, deleting, reordering, or moving any covered source
message invalidates the summary, as do incompatible schema or policy changes.
The regression is
`test_summary_store_rejects_changed_source_but_accepts_later_append`.

## Priority backlog

1. **Durable attempt IDs and lost-response idempotency — shipped in Gate 3.** One
   logical generation attempt produces at most one durable authored turn,
   including after a server commit followed by a lost HTTP response and client
   replay. IDs are project/session scoped, and exact replay returns the already
   committed terminal result.
2. **Transparent operational recovery — partially shipped.** Configured fallback
   after a qualified terminal failure, indeterminate custody, reconciliation,
   and one-winner acceptance are live. Bounded same-route retry/backoff remains.
   Circuit breaking is not a committed subsystem; add it only if passive failure
   evidence demonstrates a need. Cancellation never proves non-execution or
   non-billing, and unknown work is never silently retried.
3. **Selective old-passage retrieval.** Combine recent authored turns,
   structured canon, the source-linked rolling summary, and retrieved original
   passages for old callbacks. Preserve source IDs and branch provenance.
4. **Qualification expansion — impatient browser/API layer shipped.** Playwright
   CI and prominent reliability-toggle coverage shipped in Gate 3. The
   operational-loop release added browser cases for reload/lost-acknowledgement
   recovery, rapid double-submit, and cross-tab cleanup, plus application cases
   for exact post-acceptance replay and two-tab one-winner CAS. The broader
   failure-path synthetic writer in `SYNTHETIC_QUALIFICATION.md` remains.
5. **Per-model/provider context-budget policy — characterization started; policy
   deferred.** `provider_overhead_tokens` and
   `output_reserve_tokens` are global constants, and the second promotion of this
   campaign showed the first one is load-bearing in a way nobody has stated.
   Declaring Orion's true serving window of 24,576 makes Marginalia refuse the
   model outright: `24576 - 8000` output reserve leaves 16,576, minus the 16,000
   global overhead leaves 576 application tokens, below the 4,000 floor. The
   window metadata is therefore currently undeclared for Orion, and its admission
   ceiling is approximated by the Ollama `num_ctx` instead of enforced.

   The work is to let both reservations vary by model/provider, keeping the
   current global values as defaults, and to refuse impossible combinations
   explicitly rather than arriving at an unusable budget by arithmetic.

   **Do not simply retune 16,000 to a prettier number.** Nobody can currently say
   what that reservation represents. It is subtracted from the project input
   target to produce the application budget, and `predicted_provider_tokens` adds
   it back for telemetry, but it has never been validated against a provider's
   own reported `prompt_tokens`. That is now measurable: the OpenRouter path
   returns real usage, so the reservation can be characterised from evidence
   before it is parameterised.

   A universal 8,000-token output reserve is separately questionable now that
   reasoning models are in the menu. Reasoning bills as completion tokens, and
   Orion spent ~2,900 of them on a two-sentence answer, so the reserve is not
   bounding what it appears to bound.

   The acceptance test is behavioural, not arithmetic: a declared serving window
   must actually bound prompt plus generated *and reasoning* tokens against the
   real endpoint, rather than merely satisfying Marginalia's internal formula.
   Successful observed prompts establish only a tested lower bound. Keep that
   evidence distinct from a provider-published maximum, a configured ceiling,
   and the empirical reserve chosen for safe operation. Failures and censored
   timeouts belong in the characterization set; 30 successes is merely a
   suggestion threshold, not tail-latency qualification.

6. **Canonical Ollama GPU boundary qualification.** Ollama snap 0.32.14's
   canonical `snap.ollama.listener.service` can discover the RTX 5060 Ti through
   `cuda_v13`, but parallel invocations are moved into transient snap scopes and
   discover CPU only—even with the same AppArmor label and an isolated network
   namespace. The scope difference is observed, but scope-based device denial is
   only a hypothesis. Forced `cuda_v12` is specifically disqualified
   (`size_vram=0`). One bounded canonical `11434` request then selected cuda-v13
   but timed out refreshing GPU memory, reused stale 2.1/2.5 GiB values while
   NVIDIA reported 15,845 MiB free, and failed `cudaMemGetInfo`/allocation before
   placing any layer. Repair and requalify the owning Ollama/CUDA boundary, or
   replace the snap deployment with a separately qualified service. Do not
   treat successful CPU controls as evidence for the GPU route.

   A later host check showed that the RM/GSP enumeration failure was transient:
   `nvidia-smi` again reported the RTX 5060 Ti with 15,845 MiB free. However, one
   bounded host CUDA probe then failed `cuCtxCreate_v2` with
   `CUDA_ERROR_OUT_OF_MEMORY` before any allocation. Physical-memory
   fragmentation remains plausible but unproven. No model canary or Orion retry
   is permitted until a fresh host CUDA context and allocation/free both pass.

6. **Open fictional ontology — base protections shipped; richer classification
   deferred.** The canon
   authority boundary (see `RELIABILITY.md`) refuses an interpretation as a
   mutation warrant. It does not yet catch the step *before* that one, where a
   pass narrows an authoritative generic claim because no other members of the
   category have appeared on the page yet.

   The failure shape:

   ```
   authoritative generic category claim
       -> pass observes the current instances
       -> pass assumes a closed world          <-- introduced here
       -> pass narrows the category
       -> narrowed reading becomes a repair warrant
   ```

   `Observed(C) ⊆ C` does not license `Observed(C) = C`. In the incident that
   produced the boundary, the author's rule quantified over robots; the story had
   so far shown robots of one kind; the debugger argued the distinction was
   immaterial *in this story* and used that to dismiss a scope question rather
   than raise one. The author corrected it.

   This is not pedantry, because fiction leaves ontology open deliberately.
   Deliberate mystery, delayed exposition, foreshadowing, hidden subclasses, and
   later refinement all look like overbroad canon to a closed-world reader. A
   rule stated over AI before the story explains the postwar legal regime is not
   secretly a rule about the one AI currently on the page.

   Three cases must not collapse into each other:

   - **refinement** — later material adds subclasses or history and the earlier
     claim stays true; no defect;
   - **restriction/supersession** — later authoritative material explicitly
     narrows or replaces the earlier rule;
   - **contradiction** — the two genuinely cannot coexist at the same scope,
     time, and world state.

   Do not assume a universal open- or closed-world logic for fiction; both are
   authorial choices. The tractable question is what evidence closes a category:
   explicit enumeration, an authoritative "only", a sealed taxonomy, or a rule
   asserting no other members. Absent one, default to non-exhaustion — the cost
   is asymmetric, since a missed narrowing is a diagnostic not raised while a
   wrong narrowing edits the author's canon.

   Regression coverage to add when this is picked up: a generic rule with one
   observed instance; observed subclasses wrongly unioned into the whole
   category; a new subclass that does *not* contradict the earlier rule; a later
   narrowing that genuinely supersedes it; explicit enumeration where closed-world
   reasoning **is** permitted; and a model-derived narrowing that tries to
   authorize a repair and must be refused.

   Deferred rather than built, and recorded in the constellation item
   `skunkworks/research/DEFERRED-SEMANTIC-AUTHORITY-NONAMPLIFICATION-2026-09-05.md`
   §8, because the closure question is not fiction-specific and should be
   crosswalked against existing open/closed-world and refinement work before
   anything is invented here.

7. **TEMPORALIZED-CANON — deferred campaign function.** Fiction carries four
   distinct orderings that Marginalia currently flattens: when a fact is true
   in-world, when a character knows it, when the reader is shown it, and when the
   author recorded it. Narrative order is not world-time order, and later
   disclosure is not later validity.

   Today the product enforces only the coarse guard — registration order
   authorizes nothing, and a `contradictory_state` candidate needs canon to
   explicitly retire the anchor it targets. That stops a bad repair promoting. It
   does not let Marginalia tell a temporal transition from a contradiction, a
   late revelation from a retcon, or two characters disagreeing from canon
   disagreeing with itself.

   The intended feature is narrow: optionally preserve enough temporal structure
   to stop treating change, delayed revelation, unequal knowledge, and nonlinear
   narration as ordinary contradiction. Optional and progressively structured —
   partial orders and event anchors (`before WAR_END`, `during COLLEGE`), not
   mandatory calendars; claim-level validity rather than one mutable time field
   per character; `validity: unspecified` as the permanent default. Existing
   projects stay valid with no temporal metadata and nothing is migrated.

   Explicitly **not** a temporal-logic project, not an epistemic-logic engine,
   and not automatic retcon detection from model inference. A model-derived
   temporal relation stays a diagnostic: the authority boundary applies
   unchanged.

   Full capture, including the twelve required regression fixtures, dependencies,
   expected artifacts, and activation criteria, is in
   `research/MARGINALIA-TEMPORALIZED-CANON-DEFERRED-CAMPAIGN-FUNCTION.md`.
   Deferred; shares a boundary with item 6 and should probably be picked up
   alongside it.

8. **SUBMIT-TO-AGENT — operator-originated diagnostic handoff.** Captured as a
   campaign to schedule, not deferred research. Log tailing catches mechanical
   failures well — a live session caught two 502s and their timings *were* the
   diagnosis. It catches semantic failures not at all: canon applied to the wrong
   entity, an agent's own constraint projected onto the author, a derived reading
   reported as a defect in the author's material. Every one of those was a
   mechanically successful request that produced no log line, and every one
   reached the debugger because a human relayed it in prose.

   The author knows which occurrence is wrong and knows something about it that
   cannot be reconstructed from telemetry. The campaign gives them a way to
   contribute that: a **Submit to agent** control plus free-text testimony, which
   freezes an evidence envelope bound to the historical occurrence — session and
   canon revisions, receipt and attempt identity, provider and build identity —
   and hands it to an abstract repair endpoint.

   Two properties are load-bearing. The envelope reconstructs *the occurrence the
   author saw*, not current state. And **reporting a defect is not authorization
   to repair it**: diagnosis, proposal, and promotion gates stay in force, which
   is the evidence-versus-warrant distinction the canon authority boundary
   already implements. The endpoint is abstract — Claude Code today, something
   else later — with the author-facing workflow unchanged either way, mirroring
   how the provider catalog already handles writing models.

   Most of the envelope already exists; the work is binding it, not inventing it.
   Cheapest sequencing is after backlog item 1, since durable attempt IDs are the
   natural anchor for occurrence identity. The clarification round-trip is the
   most valuable slice and could ship alone.

   Full capture, including the twelve acceptance cases, security boundary,
   dependencies, and expected artifacts, is in
   `research/MARGINALIA-SUBMIT-TO-AGENT-CAMPAIGN.md`. The broader constellation
   pattern is recorded separately and stays deferred.

9. **Adversarial second pass — design note, risk-triggered.** The canon
   authority boundary decides whether a finding may change canon. It cannot judge
   whether the finding is any good, and nothing currently reviews a
   model-produced interpretation before it becomes a durable proposal. In the
   incident that produced the boundary, several confident readings of the
   author's world rules each looked like a defect and none was; the boundary held
   only because the author kept correcting them, which is the wrong reviewer.

   The pattern is asymmetric, not a debate. **Producer:** here is my result and
   its warrants. **Challenger:** find unsupported semantic strengthening,
   authority laundering, missing evidence, or lossy transformation — and do not
   propose improvements unless you can point at a defect. That last clause is the
   design; an unconstrained second model invents a clever alternate reading and
   calls it debugging, which is the failure being guarded against.

   **Two models agreeing is not canon, and two disagreeing is not a defect.**
   Both outputs are evidence about the transformation, never authority over the
   author, and the challenger's own findings are model-derived and bound by the
   same warrant rules. Outcomes are typed onto the existing warrant sets:
   `AGREE / independently supported`, `DISAGREE / semantic interpretation`
   (diagnostic-only), `DISAGREE / source-fidelity defect` (mutation-admissible),
   `INSUFFICIENT WARRANT`, `NEEDS AUTHOR RESOLUTION`.

   Spend it on canon promotion and repair, contradiction-versus-refinement
   classification, author-statement extraction, context summarization, and
   submitted-to-agent diagnoses — not prose generation, retrieval, CRUD, or
   ordinary turns. Prefer a challenger from a different model family, since two
   instances of one model share blind spots; the provider catalog already
   supports this, and `purpose: "context-maintenance"` shows the shape a
   `purpose: "challenger"` entry would take. Deterministic gates run first —
   nothing a schema check can catch should reach a challenger.

   Off by default, risk-triggered. Cheapest alongside item 8, where one model
   diagnoses an incident and another hostile-reads the diagnosis before anything
   promotes. Full note, including open questions, is in
   `research/MARGINALIA-ADVERSARIAL-SECOND-PASS-DESIGN-NOTE.md`.

10. **MAINTENANCE-OFF-CRITICAL-PATH — foundations shipped; durable scheduler
    remains.** Gate 1 shipped per-session coalescing, bounded chunk concurrency,
    monotone requirement joins, terminal-failure handling, and startup recovery.
    Those are real scheduling capabilities, but jobs and progress are still
    process-local. The remaining campaign is durable cross-process/restart
    single-flight, prefix-qualified snapshot promotion normally ahead of the
    foreground turn, durable progress visibility, and diagnosis of the observed
    29 provider calls. This is the successor to the planner/executor campaign,
    which made maintenance correct but can still leave it in front of the writer.
    Observed 2026-09-06 on a 102-message session: six minutes of a writing
    application declining to write, five of them spent telling the author to
    retry, then two completions at 05:29 and 05:40. Not a wedge — it recovers —
    but stop-the-world, and it scales with the manuscript.

    > Context maintenance should be incremental, amortized, and normally off the
    > user's critical path. A foreground turn blocks only when there is literally
    > no qualified context snapshot that can safely admit it.

    A snapshot should assert *qualified through revision N*, with everything
    after N an ordinary bounded tail, and maintenance running ahead of necessity
    against the existing revision CAS. A job that truthfully summarizes through
    102 while the session sits at 104 must **promote through 102 and leave
    103–104 in the tail** — prefix semantics already permit this, so the gap is
    in the scheduler, not the data model. Plus single-flight workers, so five
    retries do not start five jobs.

    Diagnose before optimizing: ask why 102 messages needs **29 provider calls**
    at all. If the chunk size exists because the Claude Code CLI has a small safe
    envelope, an implementation constraint has become product architecture. Flash
    handling a comparable payload in ~37s suggests most of that cost is
    orchestration tax, not work.

    **Do not flip the maintenance provider as the fix.** §9 of the campaign doc
    records an independent prerequisite: any maintenance backend change needs a
    qualification corpus first — schema adherence, fact preservation, no ontology
    broadening, conflict preserved as uncertainty. Maintenance writes
    consequential derived state the author never sees. A faster unqualified
    backend is a quicker route to a corrupted story bible.

    `research/MARGINALIA-MAINTENANCE-OFF-CRITICAL-PATH-CAMPAIGN.md`.

11. **Cost telemetry — passive per-turn baseline shipped.** Accepted responses
    retain the actual provider/model and normalized reported usage. The writing
    UI distinguishes known, configured-estimate, and unavailable provider cost
    without letting economics affect admission. Aggregation by
    session/project/model/provider/day and exact gateway-reported cost remain
    later work. The pinned Agent Governor v1 result currently normalizes usage to
    three token counters, so richer gateway fields require an explicit companion
    contract decision rather than an undocumented side channel. The OpenRouter
    path returns real normalized usage. Future expansion should retain raw
    provider usage too, keep reasoning tokens as
    their own field (one measured GLM 5.3 turn spent 5,310 of 5,526 completion
    tokens on reasoning), and aggregate by session/project/model/provider/day.

    Two payoffs. The author sees that GLM 5.3 full is $0.047 per turn at this
    session's size against $0.0018 for Flash and free for Orion — a 26× choice
    worth showing at selection time. And it finally makes
    `provider_overhead_tokens = 16000` measurable rather than assumed: one
    request Marginalia sized at ~19,600 application tokens came back reported as
    15,937 prompt tokens. Collecting that systematically turns backlog item 5
    from a redesign into a measurement.

    Keep billing telemetry conceptually separate from context admission. Same
    observations, different contracts — one is economics, the other a safety
    boundary, and an admission budget that starts optimising for cost is no
    longer a safety boundary. `research/MARGINALIA-COST-TELEMETRY-DESIGN-NOTE.md`.

## Operational follow-ups after the 2026-09-07 release

Wait for Erin's ordinary writing trial before starting another feature campaign.
Collect content-free outcome, latency, model, normalized usage, and cost-state
records plus her explicit report about timeout wording, recovery clarity, voice,
and usefulness. A successful prompt remains only a tested lower bound on a
provider window.

- **Unknown-attempt admission policy.** Exact replay uses the saved client
  request ID and is safe. A caller can still submit a *new* client request ID at
  the same session revision while an earlier dispatch is unknown. That may be
  legitimate distinct two-tab work, or an unsafe manual retry that causes a
  second execution/bill before revision CAS chooses one winner. Before adding
  worker concurrency or automatic retry, define whether an unknown dispatch
  blocks new same-revision admission, requires an explicit override, or uses a
  narrowly defined duplicate-intent check. Do not infer intent from prompt text
  alone, and do not weaken the existing one-winner acceptance CAS.
- **CI action runtime.** The pinned `actions/setup-node` revision currently emits
  GitHub's Node.js 20 deprecation warning even though the job installs Node 24.
  Review and pin a compatible published action revision as an independent CI
  maintenance change; it is not a Marginalia runtime defect.
- **Docket temporary-path investigation.** Keep the unproven shared-resource
  suspicion in `docs/gate3/GATE3A-QUALIFICATION.md` tracked. Reproduce under the
  original full-suite conditions before changing Docket; an isolated pass is
  not evidence of a root cause.

Counter identity on stored summaries and per-model context capacity were
previously listed here; both are now implemented. Summaries and checkpoints
record the counter that produced them, a checkpoint is not reused across a
counter change, and `context-plan` reports `counter_changed` instead of
asserting readiness it cannot prove. A model may declare `context_window_tokens`,
which narrows the effective input ceiling for that request across admission,
maintenance planning, and operator reporting; a window too small for the
project's floors is refused as a typed outcome before the provider is launched.
Both are backward compatible: records without identity are unknown rather than
mismatched, and models without a declared window stay unconstrained.

Do not combine these into one redesign. Attempt identity is the correctness
foundation for invisible retry and failover, so it comes first.

## Evidence to collect before choosing the next campaign

- context allocation, latency, and terminal failure class by provider/model;
- summary-maintenance frequency and whether unchanged prefixes are reused;
- writer-visible retries, abandoned prompts, and reload/double-submit reports;
- continuity corrections involving older summarized material;
- provider/daemon availability around deployment activity;
- human feedback about voice and quality before enabling automatic model
  fallback.

Telemetry must remain content-free. Human product validation is separate from
deterministic qualification and synthetic behavioral fuzzing.

## Cold-start checklist

1. Read `README.md`, `ARCHITECTURE.md`, `docs/RELIABILITY.md`, this file, and the
   relevant operations/provider documentation.
2. Check `git status`, the current deployment image ID, `/health/ready`, and
   `governor.execution.in_flight` before touching the live appliance.
3. Confirm the writer is not active before any container replacement. A prior
   development replacement caused a short, correctly classified AG transport
   outage.
3a. After any container replacement, check `/v1/models` availability, not only
   `/health/ready`. A host CLI that self-updates invalidates a version-pinned
   bind mount, and the running container keeps its original inode until the next
   replacement, so an unrelated-looking restart is when the provider actually
   disappears. `/health/ready` stays green throughout, because the Codex backend
   short-circuits its reachability check. This exact sequence took the Claude
   writing and context-maintenance routes offline for about sixteen hours before
   an attempted maintenance run surfaced it; mount the installer's stable
   launcher symlink instead of a version directory.
4. Create and restore-test a fresh workspace backup before live data migration
   or context-policy changes. Never restore over the live volume.
5. Reproduce new failures with provider-boundary fakes; never wait for the real
   240-second timeout or use live story text in controls.
6. Run the unfiltered suite against the exact qualified Agent Governor checkout,
   plus lint, format, package, JavaScript, container import, and CLI smokes.
7. Keep operational diagnostics and test hooks out of narrative state and out of
   writer-facing prose.

## Known limitations at this baseline

- Durable custody, exact replay, configured qualified-failure fallback, and
  reconnectable inspection are implemented. Bounded same-route retry/backoff is
  not; circuit breaking remains conditional on observed need.
- The prompt is a browser draft only until the server returns custody. Once
  custody is established the operational attempt is durable and cross-process,
  but draft text itself does not synchronize across devices.
- Selective old-passage retrieval is not implemented.
- Basic Playwright CI is present. The full synthetic failure matrix remains to be
  expanded beyond reload, lost acknowledgement, double-submit, and two-tab CAS.
- Removed-container stdout is not retained unless deployment-level log retention
  is configured.
- Source binding validates summary provenance, not literary quality; writers
  remain the authority on voice, continuity, and usefulness.
- A declared context window is trusted as configured. Marginalia does not
  discover a model's real window, so a wrong or absent `context_window_tokens`
  still permits an oversized launch that only the provider can reject.
- The enrolled Orion route is retained but not production-ready: full-path CPU
  controls pass, while the canonical cuda-v13 route has a definitive
  memory-discovery/startup failure. Production generation remains paused until
  `11434` reports nonzero VRAM for the exact tag/context or another deployment
  boundary is explicitly qualified. The different transient scope is diagnostic
  evidence, not yet a proven explanation for CPU-only parallel listeners.
- Maintenance progress is process-local, but startup now reconciles it: a
  bounded number of sessions whose durable checkpoint is ahead of their summary
  are rescheduled when the application starts, so a container replacement
  mid-maintenance no longer waits for the writer's next attempt. `active_tasks`
  still resets to zero on replacement.
- `LibraryStore` refuses to overwrite a `library.json` that changed outside the
  running application, raising `LibraryConcurrentModificationError` rather than
  silently dropping an external edit. It still holds cached state and rewrites
  the file wholesale, so external edits require an application restart to be
  picked up — refusing is the guard, not merging.
