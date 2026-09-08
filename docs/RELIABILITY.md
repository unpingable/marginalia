# Generation reliability contract

## Durable request lifecycle

```text
logical request -> reserved dispatch -> executing
                                  |-> candidate -> accepted / blocked
                                  |-> failed -> authorized fallback dispatch
                                  |-> unknown -> reconciliation only
```

One client request ID binds frozen logical work: project/session, source
revision, canon and guidance fingerprints, original model/route, authorized
fallback policy, context/body, and relevant settings. Retries and fallbacks are
new dispatches with immutable identities for their actual route, model, body,
and ordinal.

Reload, a lost browser acknowledgement, double-submit, or a second tab reconnect
to the same logical request. They do not create a second dispatch. Candidate
acceptance is idempotent by candidate identity.

## Timeout and unknown are different

The catalog provides separate connection, read-idle, and total provider bounds.
All are explicit; total timeout is at most 1800 seconds. The worker/providerctl
envelope is longer (normally 1830 seconds) so an inner provider result can be
recorded before the caller stops waiting.

A browser or worker wait ending does not prove provider cancellation, non-
execution, or non-billing. If exact outcome evidence is unavailable, the
dispatch becomes `unknown`, stays visible, and is unsafe to retry. A replacement
worker can recover Docket custody and evidence but cannot necessarily resume a
lost HTTP generation.

The writer sees a distinct unknown state explaining that Marginalia is retaining
custody. Inspection and reconciliation remain available even when the project
dispatch switch is off. An exact fetched response enters candidate acceptance;
it does not establish that another execution is safe.

## Failure and fallback

Fallback is allowed only after a dispatch is confirmed failed and only to an
entry frozen in the logical request's authorized fallback policy. It is bounded
by that list. Unknown work cannot fall back. Circuit breaking is not part of the
current contract; add it only if observed failures establish a need.

Provider transport errors, malformed bodies, policy refusal, timeout, unknown,
and stale acceptance are operational records. They cannot become assistant
prose, canon, artifacts, or later model context.

## Acceptance authority

Provider completion and durable evidence are necessary but not sufficient.
Conversation acceptance:

1. acquires the cross-process project-state lock and then the session lock;
2. detects an existing insertion by candidate identity first;
3. checks session revision, accepted-canon fingerprint, and project-guidance
   fingerprint;
4. appends prompt and authored response with a crash-safe session replacement;
5. records candidate acceptance idempotently.

Every supported canon and project-guidance writer participates in the same lock
order. Any external editing path that cannot participate must stop acceptance.

Model-proposed canon or ontology changes remain proposals. Restrictions and
contradictions never modify accepted canon automatically. Repair submission
checks the operator's actual permission. Adversarial review is advisory unless a
separate explicit policy grants more authority.

## Context retention and maintenance

Recent authored turns, accepted canon, project direction, pinned passages, the
pending prompt, and any source-covered compatible summary are mandatory inputs.
If they cannot fit the configured window, generation blocks with an actionable
explanation and requires author-approved adjustment. Accepted facts are never
silently discarded.

Maintenance and synthetic generation use the same ag-ng authorization, Docket
custody, unknown-state, and encrypted-evidence pipeline, but their purpose makes
them ineligible for conversation acceptance. A derived artifact is recorded
before its candidate is marked consumed. Successful historical consumption is
resolved before later canon changes are considered.

Existing maintenance coalescing, per-session concurrency, retry delays, recovery,
and snapshot validation are shipped. Remaining scheduler work is a durable,
operator-visible queue across process/host replacement with explicit cadence,
backpressure, and missed-run policy; it is not required for retrieval work.

## Usage, cost, and window evidence

Every accepted response records actual provider/model identity, latency,
pre-dispatch estimated prompt tokens when available, and provider-reported or
normalized usage. Missing usage stays unavailable, never zero. Cost is shown as
known, estimated, or unavailable as described in
[MODEL_PROVIDERS.md](MODEL_PROVIDERS.md).

Passive characterization includes successes, failures, censored timeouts,
estimated-versus-observed prompt deltas, and latency. Thirty successes may
justify a suggestion; they do not qualify tail latency or a maximum window.

## Evidence security and expiry

Exact provider response bodies are encrypted at rest in the application evidence
store. Access is limited to the executor and application reconciliation paths and
is audit logged. Live body expiry removes the live ciphertext body according to
retention policy while retaining non-secret custody metadata. It does not erase
ciphertext already retained in backups.

Keys are outside the data volume and Git, versioned, and separately recoverable.
Qualification must restore ciphertext and the separately supplied key into an
isolated location and decrypt a sample. See
[EVIDENCE-SECURITY.md](gate3/EVIDENCE-SECURITY.md).
