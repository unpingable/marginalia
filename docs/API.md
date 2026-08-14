# Marginalia M1 API

This is the ordinary fiction-product surface. Mutating endpoints require
`Authorization: Bearer <token>` when `GOVERNOR_AUTH_TOKEN` is set.

## Creative project

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/project` | GET | Read the active context's project brief, collaborator stance, and voice/style guidance |
| `/v1/project` | PUT | Atomically update all three fields, optionally with `expected_version` |
| `/v1/project/export` | GET | Export writer-facing project direction, story bible, conversations, and draft revisions |

The project file is context-bound. A stale update returns `409`; a file whose
embedded context does not match the active context also fails closed.

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
| `/sessions/` | GET / POST | List or create conversations |
| `/sessions/{id}` | GET / PATCH / DELETE | Read, retitle, or remove one conversation |
| `/sessions/{id}/messages` | POST | Persist one conversation message |

## Story bible and canon capture

| Endpoint | Method | Purpose |
|---|---|---|
| `/governor/fiction/characters` | GET / POST | List or add Characters |
| `/governor/fiction/characters/{id}` | DELETE | Remove a Character |
| `/governor/fiction/world-rules` | GET / POST | List or add World Rules |
| `/governor/fiction/forbidden` | GET / POST | List or add negative constraints |
| `/governor/fiction/capture/scan` | POST | Find canon candidates in a response |
| `/governor/fiction/captures` | GET | List pending canon candidates |
| `/governor/fiction/capture/{id}/accept` | POST | Accept a candidate into canon |
| `/governor/fiction/capture/{id}/reject` | POST | Dismiss a candidate |

The historical `/governor` naming is an internal package/API migration seam;
the served product language is Story Bible and canon.

## Draft artifacts

| Endpoint | Method | Purpose |
|---|---|---|
| `/governor/artifacts` | GET / POST | List or create drafts |
| `/governor/artifacts/{id}` | GET / PUT / DELETE | Read, revise, or remove a draft |
| `/governor/artifacts/{id}/version/{version}` | GET | Read a historical revision |

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
