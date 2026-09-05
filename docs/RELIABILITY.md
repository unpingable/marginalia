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
  defaults to the smaller of 30 seconds and the execution deadline.
- `MARGINALIA_GOVERNOR_INVOCATION_TIMEOUT_SECONDS` is the hard process-tree
  envelope and must exceed the largest configured provider deadline plus its
  cleanup allowance.
- `MARGINALIA_GOVERNOR_CHAT_TIMEOUT_SECONDS` bounds RPC queueing and the full
  Agent Governor reply and must exceed the invocation envelope.
- `MARGINALIA_GOVERNOR_WEDGE_SECONDS` controls when cheap readiness considers
  an in-flight invocation wedged. It should fall between the normal provider
  deadline and the RPC deadline.

Cancellation closes network streams and stale RPC framing. Local command and
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
reuse unchanged inputs. Merge outputs have hard structured compaction limits. It may leave a `.work.json`
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

Background context maintenance retries checkpointed work. This release does not
automatically retry or switch writing models. That remains a future
attempt-orchestration layer and must preserve one logical attempt ID, the same
CAS boundary, and the rule that blocks, cancellations, conflicts, and invalid
state are not silently retried.
