# Marginalia M1.5 — UX acceptance and distribution reconnaissance

Date: 2026-08-14

Tested product branch: `marginalia-m1`

Tested committed baseline: `2d4ee86c0a5c1b89a4410a13fca4acbc1c741385`

The browser run also included the local, uncommitted Codex-runtime and safe
Markdown-rendering repairs already present before this acceptance pass. This
pass did not alter product behavior in response to its findings.

## Verdict

**PRODUCT ACCEPTANCE: PASS WITH RELEASE-BLOCKING GAPS**

**DISTRIBUTION: NOT YET NORMAL-USER INSTALLABLE**

Marginalia already presents as an intentional creative-writing product. Its
core writing loop, project direction, accepted Story bible, conversations,
drafts, export, real governed provider path, and restart durability all worked
in the running appliance.

It does not yet have a complete path from the public repository to that
working appliance. The release workflow, package metadata, source staging,
default branch, provider bootstrap, and image publication currently describe
several partial paths rather than one supported installation.

## Acceptance environment

- Headless Chromium 148 driven through Playwright 1.58.2.
- Isolated Compose project and volumes on `http://localhost:8011`.
- Fiction context `m1-5-acceptance`.
- Real Codex provider through the authoritative AG daemon; no provider mock.
- Desktop viewport `1440 × 1000`, plus a `1024 × 768` compact-layout audit.
- The acceptance container was restarted between phases without removing its
  data volume.
- The existing user instance on port 8000 was not used or mutated.

Raw results:

- [`m1.5-acceptance/phase1-results.json`](m1.5-acceptance/phase1-results.json)
- [`m1.5-acceptance/phase2-results.json`](m1.5-acceptance/phase2-results.json)

## Workflow results

| User workflow | Result | Evidence |
| --- | --- | --- |
| Open an intentional empty writing room | Pass | No donor/operator vocabulary; clear project and writing affordances |
| Set brief, collaborator stance, and voice guidance | Pass | One ordinary Project direction form and one save action |
| Create/open a conversation | Pass | Conversation was created and reopened after restart |
| Generate governed fiction with the real provider | Pass | Codex returned a 4,869-character governed response through AG |
| Render writing Markdown | Pass | Heading, emphasis, strong text, paragraphs, and blockquote rendered |
| Keep the composer usable after a long response | Qualified pass | Composer remained pinned and sendable, but stayed unnecessarily expanded |
| Add a Character manually | Pass | Character appeared immediately and persisted |
| Stress the Story bible rail | Qualified pass | 6 Characters, 4 World Rules, and 4 boundaries remained readable; vertical scroll was required; no horizontal overflow |
| Ask chat to add a Character | Authority pass / UX fail | Chat did not mutate canon, but produced no reviewable proposal card |
| Save a response as a draft | Pass | Editable draft and revision were created and survived restart |
| Export the project | Pass | Export contained 1 conversation, 6 characters, and 1 artifact |
| Restart and reload | Pass | Project direction, 4 chat messages, Markdown, Story bible, and draft all survived |
| Keep operator/research surfaces out | Pass | `/dashboard`, `/docs`, `/openapi.json`, `/v2/runs`, and `/governor/status` returned 404 |
| Use project memory at 1024px | Fail | The entire right rail disappeared and no actual Story bible/project opener existed |

The initial compact-layout assertion falsely passed because its broad text
selector matched the conversation title, which happened to contain the word
“project.” The second phase recorded the matching element and corrected the
finding.

## Severity-ranked findings

### P0 — no complete distributable installation path

Four independent probes reach the same result:

1. The wheel builds, but a clean `pip install` fails:

   ```text
   ERROR: Could not find a version that satisfies the requirement
   agent-governor==2.8.1 (from versions: 0.4.0)
   ```

   Installing the wheel and third-party lock with `--no-deps` then importing
   the application fails with `ModuleNotFoundError: No module named
   'governor'`. `receipt-v1` is also an unpublished source dependency.

2. A clean M1 checkout running `./start.sh` exits before Compose:

   ```text
   sync-deps.sh: line 16: cd: .../../../agent_gov: No such file or directory
   ```

   The working image therefore depends on a separately cloned, exactly
   positioned AG repository.

3. The public repository's symbolic `HEAD` points to `marginalia-m0` at
   `e317d962...`, not the M1 product branch at `2d4ee86...`. A normal clone does
   not obtain the product tested here.

4. Anonymous manifest inspection of both
   `ghcr.io/unpingable/marginalia:latest` and
   `ghcr.io/unpingable/phosphor:latest` was denied. There is no verified public
   prebuilt image a new user can pull.

The checked-in publication workflow cannot currently repair this:

- it still names and publishes `phosphor`;
- it checks out AG's moving default branch with no `ref`;
- it does not verify or stage the required `AG_CONTRACT_COMMIT` marker;
- it copies only `src/governor`, omitting `src/fiction_governor`;
- it has no pre-publication product or container acceptance gate.

### P1 — conversational canon proposals are unavailable in the appliance

The model observed the correct semantic boundary. It responded with a clear
`Character: Orla Finch ...` proposal and explicitly said it could not modify
the Story bible itself. The accepted Character count remained six.

The application produced no proposal card and no Edit/Add/Dismiss handoff.
Replaying the exact assistant response through the live application boundary
returned HTTP 200 with:

```json
{"captures": [], "error": "Canon capture classifier not available."}
```

The UI silently treats that error as an empty suggestion list. The classifier
does exist in the pinned AG checkout at
`src/fiction_governor/canon_capture.py`; `sync-deps.sh` and the image workflow
copy only `src/governor`, so the release composition removes it.

Even after restoring that package, pending suggestions are held in the
process-local `_pending_captures` dictionary. Static inspection therefore
shows they cannot survive a restart. Runtime restart testing of that state was
masked by the missing classifier and should be added when the package is
restored.

The intended rule remains sound:

> chat proposes → human edits or ratifies → Story bible changes

The defect is the absent handoff, not the prohibition on silent mutation.

### P1 — compact layout removes project memory controls

At `1024 × 768`, CSS removes the entire project workspace. There is no visible
Story bible, Drafts, Project, or settings control to reopen it. This makes
accepted canon and project direction inaccessible on a common laptop-width
layout.

### P2 — long prompts leave an oversized blank composer

After sending the long acceptance prompt, the empty textarea retained a
131-pixel height and the composer occupied the bottom 204 pixels of the
viewport. It remained usable, but did not shrink after clearing the prompt.

### P2 — populated Story bible is usable but dense

The rail handled 6 Characters, 4 World Rules, and 4 boundaries without
horizontal overflow (`329px` client width and scroll width). Its content grew
to `1553px` inside an `889px` viewport and scrolled normally.

The Character cards expose storage-shaped copy such as `Appearance:`,
`Voice:`, and `<name> wouldn't:` in a compressed paragraph. This is legible,
but becomes visually noisy before the project is particularly large.

### P2 — generated conversation titles truncate mechanically

The generated title was bounded to 52 characters but ended mid-word:

```text
Write an 800-word opening scene for this project. Us
```

### P2 — Save as draft works but feels provisional

The action is small and low-contrast beneath a long response, and title entry
uses a native browser prompt. It is functional and persisted correctly, but is
easy to miss and visually less finished than the rest of the writing room.

### P3 — missing favicon

Each browser load recorded one 404 console error for the favicon. No user
workflow was affected.

## What worked especially well

- The empty state, three-pane model, “Story bible,” and “Things that shouldn't
  happen” all read as writing-product language.
- Project direction is understandable without knowing AG and is visibly
  active across conversations.
- The real model response followed the supplied premise, collaborator stance,
  and prose guidance well enough to provide useful acceptance evidence.
- Long prose scrolls independently while the composer remains available.
- Raw receipts, hashes, control panels, research routes, and coding surfaces
  remain absent from the ordinary product.
- Accepted state is durable. The process/container boundary did not disturb
  project direction, conversations, canon, rules, boundaries, or drafts.
- Export produces one useful project-shaped JSON file rather than governance
  ceremony.

## Distribution recommendation

Use a **published, immutable OCI image plus a thin Marginalia launcher** for
the immediate local appliance.

Do not make `pip install marginalia` the end-user runtime path yet. A wheel is
useful for development, but it does not solve the daemon, exact AG sources,
provider executables, credentials, persistence, process supervision, or
browser launch. Do not package Electron/Tauri yet either; no product need
currently justifies acquiring a second UI/runtime stack.

### Appliance boundary

```text
Marginalia launcher
  ├── pulls one immutable Marginalia image
  ├── owns start / stop / update / doctor / open-browser
  ├── creates persistent data and provider-config volumes
  └── performs host-provider preflight
        ↓
Marginalia image
  ├── Marginalia application
  ├── pinned full AG distribution, including fiction_governor
  ├── receipt-v1
  └── aligned daemon + application entrypoint
```

Docker/Compose can remain the substrate. It should not be the normal user's
vocabulary.

### Provider ownership

The AG daemon should remain authoritative. Provider setup should become a
small Marginalia first-run flow backed by the local launcher/supervisor:

1. User chooses one qualified provider.
2. Marginalia checks its real prerequisites.
3. API credentials, when used, are stored in a dedicated local secret/config
   location, never in project state or project export.
4. For a subscription CLI provider, the launcher detects or initiates the
   provider's normal host login and mounts only the required credential file
   read-only while retaining a separate writable runtime home.
5. The supervisor starts/restarts AG with that configuration.
6. Marginalia reads the daemon's actual provider identity and declares the
   writing service ready only after the authoritative health check succeeds.

This preserves the M0 truthfulness rule: the UI never displays a model switch
that controls only a local `ChatBridge`.

Qualify one provider path first. The current real-product evidence supports a
Codex-first appliance; additional API-key and Ollama paths should be added only
with the same clean-machine acceptance test.

## Next bounded implementation slice

### M1.5a — one installable local appliance

1. Make the intended M1 branch/release the public repository default without
   rewriting M0/M1 history.
2. Repair the release workflow to use the Marginalia image name, check out AG
   at the exact published contract ref, verify the SHA, and install the full AG
   distribution plus receipt-v1.
3. Publish an immutable, anonymously pullable image and verify its manifest
   from an unauthenticated environment.
4. Add a thin `marginalia` launcher with `start`, `stop`, `status`, `doctor`,
   and browser-open behavior. It must require neither an AG clone nor manual
   Compose/YAML editing.
5. Add one guided Codex provider preflight/setup path and verify that the
   provider shown in the UI is the provider AG actually uses.
6. Run a clean-host acceptance job:

   ```text
   install launcher
   → start Marginalia
   → configure/login to provider
   → browser opens
   → create project
   → governed response
   → restart
   → project and conversation persist
   ```

7. Restore `fiction_governor` in the image as part of release composition and
   assert the scanner is live. Keep proposal-card UX and durable pending
   proposals as the immediately following bounded product slice.

The compact Story bible opener should be fixed before calling the appliance
laptop-ready, but it does not require a framework rewrite.

## Screenshots

- [Empty writing room](m1.5-acceptance/screenshots/01-empty-writing-room.png)
- [Project direction](m1.5-acceptance/screenshots/02-project-direction.png)
- [Long rendered response](m1.5-acceptance/screenshots/03-long-markdown-response.png)
- [Saved draft](m1.5-acceptance/screenshots/04-saved-draft.png)
- [Populated Story bible — top](m1.5-acceptance/screenshots/05-populated-story-bible-top.png)
- [Populated Story bible — bottom](m1.5-acceptance/screenshots/06-populated-story-bible-bottom.png)
- [Chat proposal without handoff](m1.5-acceptance/screenshots/07-chat-canon-proposal.png)
- [Restarted project state](m1.5-acceptance/screenshots/09-after-container-restart.png)
- [Persisted draft](m1.5-acceptance/screenshots/10-persisted-draft.png)
- [Missing compact-layout project control](m1.5-acceptance/screenshots/11-compact-layout-control-audit.png)

## Probe notes

- Browser phase 1: 22/24 checks passed. The two failures were the proposal
  card and its missing Edit action.
- Browser phase 2: 5/7 checks passed. The two failures were runtime canon
  scanner availability and compact project access.
- Safe Markdown unit tests: 2 passed.
- Ruff: passed.
- A post-acceptance rerun of the TestClient-based product suite stalled on its
  first request and was terminated; it produced no assertion failure. The same
  local product code had passed the complete 513-test suite before this frozen
  browser pass. Because this pass prohibits opportunistic repairs, the stall
  is recorded rather than diagnosed or changed here.
- Wheel/sdist build: passed in an isolated PEP 517 build environment.
- Clean dependency installation: failed at unpublished
  `agent-governor==2.8.1`, as described above.
- Clean M1 source startup: failed at the required sibling AG checkout, as
  described above.
