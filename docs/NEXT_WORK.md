# Next work

## Context-maintenance incident resolved — 2026-09-04

The generation-boundary and interactive-liveness repair is deployed. Live build
`ee1cad83de6b84045be23e697527f2ac101632ba` is healthy, context readiness is
`ready`, and operator maintenance mode is inactive.

The incident involved session `78fc7d45675f4a21` in project
`fc3b21be00c3`: revision 18, 90 messages, and 90,922 counted history tokens.
Its valid summary covered 51 messages while proactive lookahead required 62.
Repeated Claude Code maintenance calls timed out at 240 seconds, including
after the five-hour utilization window reset from 92% to 0%. Capacity-window
utilization was therefore ruled out.

A story-free synthetic reproduction used the same schema and a comparable
56-fact dense input. The old general-agent Claude invocation exceeded a
75-second test ceiling. The identical prompt completed with valid structured
output in 24.5 seconds when tools, slash commands, customizations, and session
persistence were disabled. The shared Claude local-command adapter now enforces
that pure-completion mode. Dense-child prompts retain tighter targets while
their acceptance bounds match the unchanged final 32-fact, 300-character, and
four-evidence maxima.

The existing eight chunks and seven merges were reused. Five additional
derived reductions/merges checkpointed successfully, followed by the final
source-bound summary. `context-validate` passed at 62/62 coverage. The isolated
governed Claude synthetic passed with an authority receipt. Preflight remained
one workspace, two projects, seven sessions, and 718 messages throughout;
narrative state was unchanged. Maintenance mode was removed only after every
release check passed.

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
backups, and restore rehearsal. The completed campaign qualified 676 tests
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
