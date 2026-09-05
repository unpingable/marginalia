# Next work

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
backups, and restore rehearsal. The completed campaign qualified 682 tests
against the exact Agent Governor contract commit in `AG_CONTRACT_COMMIT`.

Context summaries have prefix semantics. `observed_revision` records provenance;
it is not a validity equality check. A summary covering messages 1–51 remains
valid after messages 52+ are appended when the covered prefix IDs and content
hash are unchanged. Editing, deleting, reordering, or moving any covered source
message invalidates the summary, as do incompatible schema or policy changes.
The regression is
`test_summary_store_rejects_changed_source_but_accepts_later_append`.

## Priority backlog

1. **Durable attempt IDs and lost-response idempotency.** Establish one logical
   generation attempt to at most one durable authored turn, including a server
   commit followed by a lost HTTP response and client replay. Scope IDs by
   session/workspace and return an already committed terminal result on replay.
2. **Transparent operational recovery.** Add bounded same-route retry for
   retryable timeout/transport failures, then configured Claude fallback. Keep
   all branches under one durable attempt ID and commit only one winner. Do not
   retry cancellation, governance blocks, invalid requests, or revision
   conflicts silently.
3. **Selective old-passage retrieval.** Combine recent authored turns,
   structured canon, the source-linked rolling summary, and retrieved original
   passages for old callbacks. Preserve source IDs and branch provenance.
4. **Qualification expansion.** Implement the impatient/failure-path synthetic
   writer described in `SYNTHETIC_QUALIFICATION.md`, then add the focused
   Playwright generation-failure flow described in `DEVELOPMENT.md` and a
   provisioned browser CI job.

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
4. Create and restore-test a fresh workspace backup before live data migration
   or context-policy changes. Never restore over the live volume.
5. Reproduce new failures with provider-boundary fakes; never wait for the real
   240-second timeout or use live story text in controls.
6. Run the unfiltered suite against the exact qualified Agent Governor checkout,
   plus lint, format, package, JavaScript, container import, and CLI smokes.
7. Keep operational diagnostics and test hooks out of narrative state and out of
   writer-facing prose.

## Known limitations at this baseline

- There is no durable attempt ledger, so a lost successful HTTP response followed
  by replay after reload can still create a logically duplicate turn.
- Automatic same-route retry, failover, circuit breaking, selective retrieval,
  and reconnectable background generation are not implemented.
- Pending prompts are browser-local drafts rather than durable cross-device
  operational attempts.
- Functional Playwright coverage and browser CI provisioning remain outstanding.
- Removed-container stdout is not retained unless deployment-level log retention
  is configured.
- Source binding validates summary provenance, not literary quality; writers
  remain the authority on voice, continuity, and usefulness.
