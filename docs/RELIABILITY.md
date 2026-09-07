# Governor/provider reliability contract

Marginalia enforces this backend-independent invariant:

> Every governed invocation either returns a valid governed outcome or fails
> within a bounded interval. It releases serialized governor capacity, closes
> or cancels network work, terminates and reaps every process it created,
> preserves stored conversation data, and returns an intelligible failure.

The service process remaining alive is a separate property. A healthy web
process does not by itself prove that governed work can make progress.

## Execution map

All supported Marginalia providers enter Agent Governor through its `codex`
backend and `/app/codex-provider.sh`. The entrypoint refuses a direct Agent
Governor Anthropic, Ollama, Claude Code, or alternate Codex command because
those legacy dependency paths bypass Marginalia's lifecycle boundary.

| Provider | Invocation | Inner deadline and cancellation | Cleanup | Caller failure | Serialized capacity / cheap health |
| --- | --- | --- | --- | --- | --- |
| Native Codex | Native CLI subprocess | `MARGINALIA_CODEX_TIMEOUT_SECONDS` (240s default); TERM/INT forwarded | New process group; TERM, 5s grace, KILL, reap | Nonzero wrapper result with timeout text | Can own AG mutation serialization; outer envelope bounds it; `/health/live` cannot detect it |
| Claude Code / Kimi Code | Typed local-command subprocess | Provider `timeout_seconds`; task cancellation propagated | New process group; TERM, 5s grace, KILL, reap | Normalized provider error, never an empty success | Same serialized path; readiness reports old in-flight work |
| Ollama / OpenAI / Kimi API | OpenAI-compatible HTTP/SSE | Separate connect, read-idle, and total execution timeouts; async cancellation closes response/client | HTTP response and owned client are closed | Classified connect/read/transport/total-deadline error | Same serialized path; readiness reports old in-flight work |
| Anthropic API | Native Messages HTTP/SSE | Same layered HTTP timeout semantics | HTTP response and owned client are closed | Same classified visible failures | Same serialized path; readiness reports old in-flight work |
| Provider wrapper | Supervised Python subprocess around every row above | `MARGINALIA_GOVERNOR_INVOCATION_TIMEOUT_SECONDS`; independent hard envelope | Enumerates descendants, TERM, bounded grace, KILL, reaps direct child | Visible hard-deadline error | Guarantees Agent Governor's backend await returns |
| Marginalia↔AG RPC | Framed Unix socket with one client lock | Ordinary and chat RPC deadlines include queue wait, connect, write, and reply | Timeout/cancellation closes stale framing; lock is always released | Typed HTTP failure or typed SSE failure event; never authored content | Prevents a caller-side lock/socket wedge; finality-gated SSE avoids AG v1 error-as-delta ambiguity |

Agent Governor's direct backend classes remain in the pinned dependency for
other Agent Governor applications, but are not a supported Marginalia runtime
path. Provider selection, credentials, and exact model identity belong in
Marginalia's typed provider catalog.

## Deadline layers

- `timeout_seconds` is the complete provider execution deadline.
- `connect_timeout_seconds` bounds HTTP connection establishment and defaults
  to the smaller of 10 seconds and the execution deadline.
- `read_timeout_seconds` is the maximum HTTP response-idle interval and
  defaults to the complete provider execution deadline.
- `MARGINALIA_GOVERNOR_INVOCATION_TIMEOUT_SECONDS` is the hard process-tree
  envelope and must exceed the largest configured provider deadline plus its
  cleanup allowance.
- `MARGINALIA_GOVERNOR_CHAT_TIMEOUT_SECONDS` bounds RPC queueing and the full
  Agent Governor reply and must exceed the invocation envelope.
- `MARGINALIA_GOVERNOR_WEDGE_SECONDS` controls when cheap readiness considers
  an in-flight invocation wedged. It should fall between the normal provider
  deadline and the RPC deadline.

Confirmed transport cancellation closes network streams and stale RPC framing. A browser or daemon RPC timeout may only stop Marginalia from waiting; it does not prove that provider execution or billing did not occur. Local command and
native subprocess cancellation also terminates their process groups. A
provider failure never creates, deletes, or rewrites a Marginalia session or
message; persistence remains an explicit application operation.

## Narrative commit concurrency

A session-backed request is generated from one persisted session revision. Its
prompt and validated authored response commit together only if a file-locked
compare-and-swap finds that same revision. Direct imports, title/model changes,
project moves, and other successful session writes advance the revision. A
conflict returns typed `stale_context` operational state and preserves the
newer durable history exactly. Session files are replaced atomically so readers
cannot observe partial JSON.

Provider, RPC, validation, and persistence exceptions receive a short incident
ID. The browser receives a bounded failure-class summary and that ID; full raw
CLI/RPC/stderr diagnostics remain in server logs under the same ID.

Marginalia does not yet offer durable request-attempt idempotency. If a server
commit succeeds but its HTTP response is lost, a client that reloads current
history can safely continue, but an automatic replay of the old delivery cannot
recover the original response by attempt ID. Correct duplicate-in-flight and
post-commit replay semantics require durable, session-scoped attempt claims and
terminal records; a request field alone would not provide the guarantee.

## Health semantics

- `/health/live` proves only that the Marginalia web process can answer HTTP.
  It deliberately performs no daemon or model work.
- `/health` performs bounded, cheap daemon contract/provider checks and
  includes `governor.execution`: in-flight count, oldest age, configured wedge
  threshold, last success/failure, and capacity-degraded state.
- `/health/ready` additionally validates durable schemas. It returns 503 if
  the bounded daemon checks fail, stored schemas are not ready, an invocation
  exceeds the wedge threshold, or an RPC capacity timeout has not yet been
  followed by successful governed progress.

Readiness does not invoke a model and therefore does not prove provider output
quality, credentials that expire after discovery, or end-to-end reply health.
That is the synthetic probe's job.

## Production synthetics

`python3 -m gov_webui.synthetic_worker` follows the existing polling-worker
deployment convention. It calls `/v1/internal/synthetic-governor`, which uses
the real governed request/provider/reply path but a dedicated
`<production-context>-synthetic` Agent Governor context. It never creates or
updates a Marginalia library project, session, or message.

Every attempt writes one compact JSON object to stdout and to
`/backups/marginalia-synthetics.jsonl`, including timestamp, requested model,
resolved backend, PASS/FAIL, latency, receipt on success, and failure class on
failure. The file and `marginalia-synthetic` container logs are the monitoring
interface; no new alerting system is implied.

A PASS establishes that the HTTP authentication boundary, serialized governor
path, selected provider invocation, authority receipt, and reply path completed
inside the probe deadline. It does not establish answer quality or the health
of a different backend.

The recommended initial production matrix is:

- local Qwen every 15 minutes for cheap global-path coverage;
- native Codex hourly for the primary subscription backend;
- Claude Code and Kimi Code once daily for their distinct command adapters;
- disabled credentialed API providers are not probed until intentionally
  enabled, avoiding API spend and credential requirements.

The worker persists last-attempt timestamps in the same JSONL record, so a
container restart does not cause an immediate duplicate probe burst.

## Authority over the author's canon

Canon is the author's. Marginalia may add to it, and may repair a record that
provably disagrees with its own source, but it may not settle a question about
what the author's words mean.

Every `CanonReviewItem` carries a **warrant** saying what makes it a change to
canon rather than an opinion about it, and warrants fall in two disjoint sets:

| set | examples | may carry an edit |
| --- | --- | --- |
| source-fidelity | `author_statement`, `source_omission`, `source_truncation`, `dropped_subject`, `dangling_reference`, `transformation_mismatch`, `category_misclassification`, `duplicate_state`, `contradictory_state` | yes |
| interpretive | `inferred_implication`, `strengthened_proposition`, `scope_interpretation`, `proposed_formalization`, `ontology_clarification`, `interpretive_ambiguity` | no |

A source-fidelity warrant can be shown true against the source without
preferring one reading of the author's prose over another: text was dropped, a
subject was lost in promotion, a record is filed as the wrong kind of thing. An
interpretive warrant exists only in a reading. Those are still worth surfacing —
they stay in the queue as diagnostics the author can answer — but promotion
refuses them, and a proposal that targets an existing anchor cannot claim
`author_statement`.

Two further gates:

- **Every relied-on proposition must be canon.** `relied_on` names what the
  candidate's argument runs through. Promotion checks each against the registry,
  so an argument cannot pass through a premise the model supplied and come out
  the other side as a repair.
- **Resolutions stick.** Dismissing or accepting a candidate records its
  evidence fingerprint — kind, subject, statement, target, whitespace- and
  case-normalised, deliberately *excluding* the warrant. Re-deriving the same
  claim by a different argument raises `CanonResolutionStandsError` rather than
  re-queuing it. Reopening needs new authoritative evidence: a different
  statement, or a different anchor.

### The failure this exists to stop

```
authoritative source proposition
    -> model-derived stronger proposition     <-- introduced here
    -> alleged contradiction
    -> proposed repair                        <-- blocked
```

Concretely, from the incident that motivated it. The author's rule:

> "Since robots have never been human, they cannot perceive ghosts."

That states an exclusion — a necessary condition at most. An analysis pass read
sufficiency into it ("having been human enables perception"), then produced a
human character with no ghost affinity as proof the rule was defective. The
author never made the stronger claim; the pass supplied it and then argued with
it. `strengthened_proposition` is interpretive, so it cannot carry an edit, and
`relied_on` would fail anyway because the sufficiency claim is not canon.

The same pass found two real defects — a world fact filed under a prohibition
category, and a promotion that dropped its subject. Both are mechanical, both
still promote normally. The boundary is not timidity; it is about what counts as
evidence.

### The observed cast is not the ontology

A rule stated over a category stays a rule over the category even when the pages
written so far show one member of it. Retrieval can only show what has been
written, so it establishes `observed_members(C) ⊆ C` and never, by itself,
`observed_members(C) = C`. Fiction withholds ontology deliberately: the subtype
introduced three chapters from now refines the world model rather than proving
an earlier generic rule was overbroad.

`canon_scope.py` answers one question against explicit author metadata — has the
author marked this category complete? Closure-sounding prose is not authority by
itself. In Possible canon, the author uses the visible “Category remains open”
control to name the category a rule closes; without that action it stays open. A strong claim about members is not a
claim about extent, so *"all robots are built from salvaged parts"* quantifies
over the category without closing it.

A candidate that sets `category` is saying its argument turns on how many
members exist. Promotion refuses it unless an accepted, still-live canon anchor carries that explicit closure. When the
author has closed it, closed-world reasoning is licensed and the candidate
promotes normally — the point is the safe default, not a prohibition.

### Registration order is not validity order

Canon is entered in whatever order the author reached for it. That is not the
order the story tells, and not the order the world lived. An anchor added today
may describe earlier world-time — backstory, disclosed history, the legal regime
that explains a rule written last week — so **later disclosure is not later
validity** and a newer anchor is not a newer truth.

A `contradictory_state` candidate targeting an anchor therefore needs canon to
explicitly retire that anchor, naming it: *"supersedes world-1"*, *"world-1 no
longer applies"*, *"instead of world-1"*. Ordinal position authorizes nothing.
This keeps refinement, supersession, and contradiction from collapsing into each
other: only the middle one is a claim about validity, and only the author makes
it.

Deliberately not modelled here: proposition identity, semantic equivalence,
fuzzy resolution matching, and any general category calculus. Those are
constellation questions — see `NEXT_WORK.md` backlog item 6 and the skunkworks
deferred item it names.

## Bounded long-fiction context

Durable session history remains complete. When a project's bounded-context
policy is enabled, Marginalia constructs provider input from counted components:

1. project direction and accepted Story Bible constraints;
2. a source-bound derived summary of an exact older message prefix, when needed;
3. the unsummarized recent authored suffix;
4. the pending user prompt.

The initial policy targets at most 48,000 predicted provider-input tokens:
32,000 application-controlled tokens plus a conservative 16,000-token
Agent Governor/provider allowance. A separate 8,000-token output reserve is
recorded for end-to-end context-window planning. Agent Governor contract v1
does not accept a native output cap on `chat.send`, so that reserve is planning
headroom rather than a provider-enforced completion limit.

Interactive admission also retains the same maintenance lookahead used by the
prebuilder: the smaller of 12,000 tokens or one third of the application
budget. With the initial policy this is 10,666 tokens, so an ordinary writing
provider call is launched only when current application-controlled input is at
most 21,334 tokens (about 37,334 predicted tokens including provider overhead).
The remaining nominal envelope is growth and maintenance headroom, not capacity
that the interactive path may silently consume while its derived summary is
behind.

Counts use a real configured tokenizer (`o200k_base` by default), optional
model-specific safety multiplication, and message framing overhead. Marginalia
preflights before launching the writing provider. Mandatory
project/canon/prompt input that cannot fit returns typed `context_too_large`.
If a valid summary does not cover the prefix required by that same lookahead,
the interactive request schedules resumable maintenance outside the request
and returns typed `context_maintenance` immediately. It launches neither the
writing provider nor the remote maintenance provider inline. Neither outcome
mutates narrative history.

A summary is derived cache state, never canon or authored prose. It records the
session/context, observed revision, every covered message ID, a SHA-256 digest
of the exact covered prefix, configured/provider/upstream model identity,
prompt-schema version, usage, and authority receipt IDs. Structured sections
separate narrative recap, character state, observed facts, unresolved threads,
time/location state, and uncertainties. Every item cites covered source message
IDs. A source edit, reorder, deletion, context mismatch, malformed schema,
foreign evidence citation, or oversized result invalidates the summary.

Maintenance runs in a dedicated `<context>-maintenance` AG context. Chunk work and bounded pairwise merge work are atomically persisted outside
sessions so interrupted prebuilds can resume and later prefix expansion can
reuse unchanged inputs. Because chunks are packed left to right and addressed by
a content digest, a later run needing *less* coverage reuses the leading chunks
it already paid for instead of discarding the checkpoint; every individual reuse
is still gated on a digest recomputed from current message content, so edited
history is re-summarised rather than reused. Merge outputs have hard structured compaction limits. It may leave a `.work.json`
operational trace after failure; this is never included in story history,
search, forks, canon capture, manuscript operations, or provider context.
Completed summaries are also derived files and can be rebuilt from durable
history.

A deployment may rename a configured maintenance model without discarding
validated checkpoint work only when the current provider catalog proves that
the old and new IDs resolve to the identical protocol, provider, and upstream
model. Prompt-version and exact source-prefix validation still apply. A changed
provider/model identity or an alias absent from the catalog fails closed and
starts independent work rather than silently reusing it.

Projects are activated independently only after required summaries validate.
After activation, successful authored commits schedule a nonblocking refresh
before the hard budget, with a bounded lookahead target and retry delays for
resumable maintenance. If maintenance nevertheless falls behind, interactive
generation fails quickly with a safe preparation message while the prompt
remains in the browser. Exactly zero narrative mutation occurs until a validated
authored result wins the existing session-revision compare-and-swap.

Required summary coverage has exactly one authority. Generation admission
measures it against the real fixed project context and the writer's actual
prompt, and that measured value travels with the failure to the component that
must satisfy it. Preparation never re-derives the same quantity from placeholder
inputs, and no proactive heuristic may suppress work an admission check has
already proven necessary: the maintenance watermark is consulted only when no
observed requirement is present. Concurrent requirements join monotonically, so
a stronger requirement arriving while weaker work holds the session slot is
recorded and satisfied by a follow-up run rather than dropped. The contract is
that the consumer computes its requirement and the preparer satisfies it; two
components independently estimating the same quantity from different inputs is
how a session can be individually correct at every step and collectively unable
to generate.

Background context maintenance retries checkpointed work, but only failures a
retry could plausibly clear. A structurally impossible source — an authored
passage larger than one maintenance chunk — and a maintenance model that is
absent or misconfigured are terminal: they are not retried on the backoff
schedule, they are logged as errors rather than falling silent, and admission
reports them as a non-retryable oversized-context outcome instead of promising
preparation that can never finish. This release does not automatically retry or
switch writing models. That remains a future attempt-orchestration layer and
must preserve one logical attempt ID, the same CAS boundary, and the rule that
blocks, cancellations, conflicts, and invalid state are not silently retried.
