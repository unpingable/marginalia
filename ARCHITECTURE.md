# Marginalia architecture (M0)

## Product boundary

```text
browser
  │ HTTP / SSE
  ▼
Marginalia FastAPI application
  │ GovernedChatAdapter (one context)
  │ JSON-RPC 2.0 / Unix socket
  ▼
Agent Governor daemon
  ├── provider/model execution
  ├── governed context and pending state
  └── authority receipt store
```

Constellation is outside this boundary. Marginalia neither imports nor calls
NQ, Nightshift, Monitor, Maude, Desk, or qualification services.

## Application-facing AG contract

`GovernedChatAdapter` owns these operations:

| Operation | AG RPC |
|---|---|
| Validate capability and state root | `governor.hello` |
| Discover provider/model | `chat.backend`, `chat.models` |
| Send/stream governed response | `chat.send`, `chat.stream` |
| Observe context pending state | `commit.pending` + `context_id` |
| Resolve in the same context | `commit.fix/revise/proceed` + `context_id` |
| Verify authority evidence | `receipts.detail` |

Every successful or blocked governed outcome must return a receipt that AG's
receipt store confirms has `receipt_role=authority`. A successful resolution
must link its evidence to both the pending ID and original blocking receipt.
Streaming deltas are provisional; no receipt is exposed until AG returns its
final governed outcome.

## State layout

For `MARGINALIA_DATA_ROOT=/data` and context `erin-writing`:

```text
/data/.governor/                         AG daemon state / context base
└── erin-writing/
    ├── _context.json
    ├── .governor/
    │   ├── continuity/anchors.json
    │   ├── pending_violations.json      present only while pending
    │   └── exceptions/
    └── sessions/                        donor conversation persistence
```

The daemon starts with `governor --root /data`, which makes its governor
directory `/data/.governor`. Marginalia configures `GovernorContextManager`
with that same directory. The AG handshake reports its resolved directory and
the adapter rejects a mismatch.

## Provider ownership

AG is authoritative. `BACKEND_TYPE`, credentials, provider host/path, and AG's
default model configure the daemon process. Marginalia queries those values;
it has no local `ChatBridge` and cannot switch the provider independently.

## Donor code retained at M0

The internal package remains `gov_webui`, and `adapter.py` still includes old
fiction, code, research, dashboard, artifact, and export routes. This avoided a
large frontend/backend rewrite during the correctness slice. Those legacy
non-chat routes directly use pinned AG modules and are the main remaining
product-cleanup seam. Governed execution, pending resolution, provider state,
and receipt authority do not use those imports.

The next product slice should remove operator/research/code surfaces from the
served application while retaining fiction canon, sessions, artifacts, and
export. It should not broaden `GovernedChatAdapter` into a platform SDK.

## Regression boundary

`tests/test_live_governed_chat_contract.py` starts a real AG daemon with a
deterministic no-network provider and proves:

```text
request → block/authority receipt → context pending on disk
        → daemon restart → pending recovered
        → wrong-context resolution rejected
        → correct-context resolution/authority receipt
        → original receipt linkage → pending cleared
```
