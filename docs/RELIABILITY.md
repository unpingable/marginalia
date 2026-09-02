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
| Marginalia↔AG RPC | Framed Unix socket with one client lock | Ordinary and chat RPC deadlines include queue wait, connect, write, and reply | Timeout/cancellation closes stale framing; lock is always released | HTTP 504 for governor timeout; stream error chunk for streaming | Prevents a caller-side lock/socket wedge; cannot cancel arbitrary AG internals by itself |

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
