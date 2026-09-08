# Marginalia architecture

## Runtime ownership

```text
browser / API
      |
      v
Marginalia web application
  | freezes logical request + application revision/canon/guidance
  v
GenerationStore ---- inspection/reconciliation remains available when dispatch is off
      |
      v
Docket worker / Marginalia executor
  | presents exact work authorization
  v
ag-providerctl -> ag-providerd -> configured provider/model
      |
      v
encrypted response evidence -> revision-checked Marginalia acceptance
```

- **ag-ng** authorizes exact work and isolates provider credentials.
- **Docket** owns attempt custody and worker recovery.
- **Marginalia executor** owns provider dispatch and response evidence.
- **Marginalia application** owns whether a response enters the story.

A worker replacement can recover custody and captured evidence. It cannot
necessarily resume a provider HTTP or command execution whose connection was
lost. Such work remains `unknown` unless exact evidence or provider-supported
reconciliation resolves it; it is never silently redispatched.

## Request and acceptance identity

One client request ID identifies frozen logical work, including the original
model/route and authorized fallback policy. Every actual dispatch has a separate
digest containing its actual model, route, request, and ordinal. Fetching a
provider response starts candidate reconciliation; it does not authorize retry.
Confirmed cancellation stops waiting but does not prove no execution or billing
occurred.

Conversation acceptance locks project state and then the session, checks for a
prior insertion by candidate identity, and only then checks current revision,
canon, and guidance before a crash-safe append. Canon and project-guidance
writers use the same cross-process project lock and lock order.

## State layout

For `MARGINALIA_DATA_ROOT=/data`:

```text
/data/.marginalia/contexts/     projects, sessions, canon, artifacts
/data/.marginalia/shared/       shared appliance state
/data/.governor/                compatibility symlink into the new layout
/data/marginalia/               compatibility symlink into the new layout
```

Startup migrates legacy roots without changing their bytes and preserves
write-through compatibility symlinks. Durable generation indices and encrypted
evidence remain application state; providerd has a separate state volume.

## Runtime services

The supported Compose deployment runs the web application, generation worker,
ag-providerd, backup worker, and synthetic probe. Durable generation is not an
optional profile. The visible per-project **ag-ng · on/off** switch controls new
dispatch only; pending inspection, reconciliation, and evidence recovery remain
available.

The web process receives only the model catalog and evidence keyring. The worker
receives the authorization issuer, evidence keyring, and providerctl identity.
Only ag-providerd receives provider API credentials or command-provider login
state. NAS bind-mounted inputs are copied to process-private, root-owned `0600`
paths before parsers start.

## Context and canon

All authored history remains durable. Provider context is a revision-bound,
token-counted projection containing required recent turns, accepted canon,
project direction, pinned passages, and a source-covered derived summary when
available. If required material cannot fit, generation blocks with an
actionable explanation; accepted facts are never silently discarded.

Context maintenance and synthetic probes use the same ag-ng/Docket custody path
with non-conversation purposes. Their candidates cannot enter a story and are
marked consumed only after the derived artifact is crash-safely stored.

Classic donor routes are frozen source history. They are absent from discovery
and cannot be re-enabled at runtime.
