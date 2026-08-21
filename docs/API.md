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
| `/v1/chat/completions` | POST | Send or stream an OpenAI-compatible governed fiction response |
| `/v1/models` | GET | Discover models advertised by AG's configured provider |
| `/v1/backends` | GET | Report the provider that actually owns governed execution |
| `/v1/backends/switch` | POST | Always returns `409`; provider configuration belongs to AG |
| `/v1/governed-chat/pending` | GET | Observe actionable pending state in the active context |
| `/v1/governed-chat/resolve` | POST | Correct, revise a rule, or explicitly proceed in that same context |

Authority receipts remain part of the application/AG correctness contract but
are not presented as ordinary writing UI.

## Conversations

| Endpoint | Method | Purpose |
|---|---|---|
| `/sessions/` | GET / POST | Search, filter, sort, or create conversations in a project |
| `/sessions/{id}` | GET / PATCH / DELETE | Read, rename, pin, archive, move, or remove one conversation |
| `/sessions/{id}/messages` | POST | Persist one conversation message |
| `/sessions/{id}/fork` | POST | Fork through `message_id`, retaining explicit parent metadata |
| `/v1/conversations/tree` | GET | Read explicit parent/child branch structure |

`GET /sessions/` supports `view=active|archived|pinned|all`, `q`, and
`sort=updated_desc|updated_asc|title`. Deletes are immediate at the API layer;
the writing-room UI requires confirmation.

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
| `/api/info` | GET | Product identity and reachable product endpoints |

## Quarantined donor routes

Old code-builder, research, dashboard, intent-compiler, raw-receipt, and generic
administration routes remain in source only to keep their historical tests
available during staged deletion. They return `404` in a normal Marginalia
runtime and are omitted from `/api/info`. The test-only
`MARGINALIA_ENABLE_DONOR_ROUTES=1` switch is not a supported product mode.
