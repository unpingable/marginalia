# Marginalia architecture (M1)

## Product boundary

```text
browser
  │ HTTP / SSE
  ▼
Marginalia FastAPI application
  ├── creative project / sessions / canon / artifacts
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
    ├── marginalia/project.json          brief / stance / voice, context-bound
    ├── .governor/
    │   ├── continuity/anchors.json
    │   ├── pending_violations.json      present only while pending
    │   └── exceptions/
    └── sessions/                        conversation persistence
```

The daemon starts with `governor --root /data`, which makes its governor
directory `/data/.governor`. Marginalia configures `GovernorContextManager`
with that same directory. The AG handshake reports its resolved directory and
the adapter rejects a mismatch.

## Provider ownership

AG is authoritative. `BACKEND_TYPE`, credentials, provider host/path, and AG's
default model configure the daemon process. Marginalia queries those values;
it has no local `ChatBridge` and cannot switch the provider independently.

## M1 product prompt path

Marginalia renders the three creative-project fields into one
`MARGINALIA_PROJECT_CONTEXT_V1` system message. That message is inserted into
the conversation sent to `GovernedChatAdapter`; AG then performs its ordinary
fiction augmentation, provider execution, response checks, pending-state
handling, and authority receipt generation. Project direction does not bypass
or compete with the governance prompt path.

The project file stores its owning context ID. A mismatch is an error, and
tests exercise both independent A/B stores and a copied-file mismatch. The live
socket regression makes a deterministic provider respond only when all three
fields arrive through AG, then separately proves the block/restart/resolve
contract.

## Quarantined donor code

The internal package remains `gov_webui`, and `adapter.py` still includes old
code, research, dashboard, and receipt routes. In M1 they are blocked by
default at the application boundary and absent from product discovery. Heavy
viewmodel/dashboard/instrument imports are conditional on the explicit donor
test switch, so they are not runtime dependencies of the normal product path.
The retained source and tests can be deleted incrementally; they do not justify
broadening `GovernedChatAdapter` into a platform SDK.

## Regression boundary

`tests/test_live_governed_chat_contract.py` starts a real AG daemon with a
deterministic no-network provider and proves:

```text
project config → governed provider response / authority receipt
request → block/authority receipt → context pending on disk
        → daemon restart → pending recovered
        → wrong-context resolution rejected
        → correct-context resolution/authority receipt
        → original receipt linkage → pending cleared
```
