# Marginalia M1 API

This is the ordinary fiction-product surface. Mutating endpoints require
`Authorization: Bearer <token>` when `GOVERNOR_AUTH_TOKEN` is set.

## Creative project

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/project` | GET | Read the active context's project brief, collaborator stance, and voice/style guidance |
| `/v1/project` | PUT | Atomically update all three fields, optionally with `expected_version` |
| `/v1/project/export` | GET | Export the complete portable project record as JSON |
| `/v1/project/export.zip` | GET | Download readable Markdown plus the complete JSON record |
| `/v1/project/snapshots` | GET / POST | List or create immutable named checkpoints |
| `/v1/project/snapshots/{id}` | GET | Verify and read one checkpoint |
| `/v1/projects` | GET / POST | List projects or create an isolated fiction project |
| `/v1/projects/{id}` | PATCH | Rename, archive, or restore a project |
| `/v1/workspaces` | GET / POST | List or create no-auth contextual household workspaces |
| `/v1/workspaces/{id}` | PATCH | Rename a workspace or configure its backup policy |
| `/v1/workspaces/{id}/backups` | GET / POST | List or create verified workspace backups |
| `/v1/workspaces/{id}/backups/{file}/verify` | POST | Verify archive members and the outer checksum |
| `/v1/workspaces/{id}/backups/{file}/restore-test` | POST | Rebuild and validate the backup in an isolated temporary root |
| `/v1/search` | GET | Search conversations, messages, artifacts, manuscript nodes, and canon |
| `/v1/entities` | GET | List accepted characters with exploration backlinks |

Project-aware endpoints accept `project_id` as a query parameter for reads or a
request field for writes. The project file is context-bound. A stale update
returns `409`; a file whose embedded context does not match the selected
context also fails closed. Existing conversations are enrolled into the
`Default project` without rewriting their session files.

## Governed conversation

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/chat/completions` | POST | Generate a governed fiction response with a typed terminal outcome |
| `/v1/models` | GET | Discover models advertised by AG's configured provider |
| `/v1/backends` | GET | Report the provider that actually owns governed execution |
| `/v1/backends/switch` | POST | Always returns `409`; provider configuration belongs to AG |
| `/v1/governed-chat/pending` | GET | Observe actionable pending state in the active context |
| `/v1/governed-chat/resolve` | POST | Correct, revise a rule, or explicitly proceed in that same context |

Authority receipts remain part of the application/AG correctness contract but
are not presented as ordinary writing UI.

Terminal outcomes are explicitly `authored`, `blocked`, or `failure`.
Failures use a non-2xx response with `failure_type`, a safe `message`,
`retryable`, and an `incident_id` that correlates with full server logs; they
never contain `choices`. A session-backed request supplies `session_id`
and exactly one new user message. Marginalia commits that prompt and the
validated authored response together only on `authored`; all other outcomes
leave durable conversation history unchanged. The final append is a durable
revision compare-and-swap. If another tab or API writer changes the session
first, Marginalia returns a `409` failure with `failure_type=stale_context`.
`stream=true` retains SSE
framing but waits for the transactional `chat.send` result before emitting
content because AG contract v1 does not type provider failures in partial
chunks.

Clients must inspect `outcome` before reading `choices`. Only `authored`
responses carry assistant choices. Blocked and failure payloads are operational
states, and SSE clients receive the same outcome discriminator after terminal
validation rather than incremental provider deltas.

## Conversations

| Endpoint | Method | Purpose |
|---|---|---|
| `/sessions/` | GET / POST | Search, filter, sort, or create conversations in a project |
| `/sessions/{id}` | GET / PATCH / DELETE | Read, rename, pin, archive, move, or remove one conversation |
| `/sessions/{id}/messages` | POST | Import one message; assistant text requires `outcome=authored` |
| `/sessions/{id}/fork` | POST | Fork through `message_id`, retaining explicit parent metadata |
| `/v1/conversations/tree` | GET | Read explicit parent/child branch structure |

`GET /sessions/` supports `view=active|archived|pinned|all`, `q`, and
`sort=updated_desc|updated_asc|title`. Deletes are immediate at the API layer;
the writing-room UI requires confirmation.

Session payloads include a monotonically increasing `revision`. Direct message
imports, title/model updates, project moves, and successful generated turns
advance it. Assistant imports require `outcome=authored`; operational strings
cannot be imported through that role without the explicit authored assertion.

## Story bible and canon capture

| Endpoint | Method | Purpose |
|---|---|---|
| `/governor/fiction/characters` | GET / POST | List or add Characters |
| `/governor/fiction/characters/{id}` | DELETE | Remove a Character |
| `/governor/fiction/world-rules` | GET / POST | List or add World Rules |
| `/governor/fiction/forbidden` | GET / POST | List or add negative constraints |
| `/governor/fiction/capture/scan` | POST | Find canon candidates in a response |
| `/governor/fiction/captures` | GET | List pending canon candidates |
| `/governor/fiction/capture/{id}` | PATCH | Correct a pending suggestion without accepting it |
| `/governor/fiction/capture/{id}/accept` | POST | Accept a candidate into canon |
| `/governor/fiction/capture/{id}/reject` | POST | Dismiss a candidate |

The historical `/governor` naming is an internal package/API migration seam;
the served product language is Canon. Model output is only a pending candidate
until the writer explicitly accepts it.

## Exploratory artifacts

| Endpoint | Method | Purpose |
|---|---|---|
| `/governor/artifacts` | GET / POST | Filter/search or create typed artifacts |
| `/governor/artifacts/{id}` | GET / PUT / PATCH / DELETE | Read, revise, organize, trash, or remove an artifact |
| `/governor/artifacts/{id}/working-copy` | PUT / DELETE | Autosave or discard mutable text without creating a revision |
| `/governor/artifacts/{id}/version/{version}` | GET | Read a historical revision |
| `/governor/artifacts/{id}/compare` | GET | Compare two committed revisions |
| `/governor/artifacts/{id}/canon-comparison` | GET | Deterministically check draft/working-copy text against accepted canon |
| `/governor/artifacts/{id}/canon-proposal` | POST | Create a provenance-linked pending canon review item |
| `/governor/artifacts/{id}/version/{version}/restore` | POST | Restore old text as a new revision |

`GET /governor/artifacts` supports `view=active|trash|all`, `q`, `status`, and
`tag`. Status values are `idea`, `drafting`, `revised`, and `final`. Trash is
reversible; hard deletion remains an explicit API operation.

Allowed `artifact_type` values are `draft`, `scene`, `character`,
`world_rule`, and `note`. A promoted output records `conversation_id`,
`source_message_ids`, and `captured_at` as immutable origin provenance. Saving
an artifact never promotes it into canon.

## Manuscript

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/manuscript` | GET / POST | List or add part/chapter/scene nodes |
| `/v1/manuscript/{id}` | PATCH / DELETE | Rename, relink, update status, or remove a node |
| `/v1/manuscript/{id}/move` | POST | Reparent/reorder a node with cycle protection |
| `/v1/manuscript/compile` | GET | Compile the current artifact text as Markdown or DOCX |

Manuscript nodes reference artifact IDs. Reordering the manuscript never
duplicates or changes artifact content.

## Service information

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Marginalia writing room |
| `/health` | GET | AG contract and provider readiness |
| `/health/live` | GET | Process liveness for restart decisions |
| `/health/ready` | GET | Provider/daemon and durable-schema readiness, with HTTP 503 on failure |
| `/v1/system` | GET | Deployment identity, schema/preflight state, and backup destination status |
| `/api/info` | GET | Product identity and reachable product endpoints |

## Quarantined donor routes

Old code-builder, research, dashboard, intent-compiler, raw-receipt, and generic
administration routes remain in source only to keep their historical tests
available during staged deletion. They return `404` in a normal Marginalia
runtime and are omitted from `/api/info`. The test-only
`MARGINALIA_ENABLE_DONOR_ROUTES=1` switch is not a supported product mode.

## Long-session generation outcomes

When a project has bounded context enabled, preflight may add these operational
failures:

| HTTP | `failure_type` | Meaning |
|---|---|---|
| 503 | `context_maintenance` | A required source-valid summary could not be prepared or loaded |
| 422 | `context_too_large` | Mandatory project/canon/prompt or recent context cannot fit the configured allocation |

Both payloads retain the normal typed failure shape and contain no `choices`.
They commit neither the pending user prompt nor assistant text. The writer's
draft stays in browser draft state for retry.

Session-backed `stream=true` requests are rejected because transactional
session commit and typed finality cannot safely expose partial output under the
current AG contract. Stateless streaming remains terminally gated. Clients
must not treat an HTTP success code, an SSE data frame, or locally rendered
status prose as authored content without `outcome=authored`.
