# Marginalia

Marginalia is a standalone governed creative-writing application. Agent
Governor (AG) is its direct execution and governance substrate; Constellation
is not part of its runtime.

```text
Marginalia → GovernedChatAdapter → Agent Governor daemon → model provider
```

No NQ, Nightshift, Monitor, Maude, Desk, qualification service, or Phosphor
control-plane service is required.

## Install the local appliance

The supported end-user path is one Marginalia launcher backed by a pinned
container image. It does not require a Python environment, an AG checkout,
Compose files, provider environment variables, or YAML editing.

After the `v0.1.0` release is published:

```bash
curl -fsSL https://github.com/unpingable/marginalia/releases/download/v0.1.0/install-marginalia.sh | sh
```

The installer starts Marginalia. On first use it guides the writer through
Codex device authentication, creates durable local writing/login volumes,
waits for the governed writing service to become healthy, and opens the
browser. Later use is deliberately mundane:

```bash
marginalia start
marginalia status
marginalia stop
marginalia doctor
```

Stopping or updating the appliance never deletes writing. Docker is the
current local substrate, but the launcher owns its vocabulary and lifecycle.
See [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) for release and clean-machine
qualification details.

## M1.5 status

This repository was extracted from the last useful pre-Desk governed-chat
state of `gov-webui` at commit
`b0c99e417363f216fd53490a31a8e6f5f485a92b`. The internal Python package is
still named `gov_webui` to keep the correctness slice reviewable; the
distribution, command, repository, UI title, runtime, and container are named
Marginalia.

M0 established and tested one trustworthy invariant:

> A governed response can be blocked in one chat context, remain durably
> pending through a daemon restart, be observed and resolved only in that
> context, and retain authoritative AG receipt linkage.

M1 replaces the served donor UI with a fiction-only writing room. The ordinary
product includes projects, managed and forkable conversations, project
direction, Characters, World Rules, negative constraints, a durable canon
review queue, versioned/autosaved artifacts, manuscript organization and
compilation, unified search, checkpoints, project export, and actionable
governed blocking. It contains no model switcher, raw receipt strip,
research dashboard, code builder, intent compiler, or operator console.

Historical donor endpoints and tests remain temporarily in `adapter.py` to
avoid making this product slice a broad backend rewrite. They are unreachable
by default, omitted from `/api/info`, and their heavy operator imports are not
loaded by the product path. `MARGINALIA_ENABLE_DONOR_ROUTES=1` exists only for
the retained compatibility suite and is not a supported product mode.

M1.5 adds a reproducible local-appliance boundary. The release image contains
the complete qualified AG distribution (including `fiction_governor`),
receipt-kernel, receipt-v1, the Marginalia application, and a pinned Codex CLI.
The thin launcher handles image retrieval, first-run login, aligned persistence,
startup health, browser opening, updates, diagnostics, and shutdown.

## Writing library and migration

Marginalia adds lightweight household workspaces above projects, conversation lifecycle, explicit forks, and artifact
provenance without rewriting the original content stores. Existing session
JSON files are enrolled under Erin's `Default project`; their message payloads
are not rewritten. Workspaces are contextual partitions with one-click
creation and switching, not authentication or security boundaries. Organization is stored atomically at
`$MARGINALIA_DATA_ROOT/marginalia/library.json`. Each additional project owns
an isolated fiction context for its direction, canon, conversations, pending
state, and artifacts.

Conversation metadata in the sidecar includes project, archived/pinned state,
and optional `parent_session_id` plus `forked_at_message_id`. Artifacts retain
their originating conversation, source message IDs, and capture timestamp.
Artifact types are Draft, Scene, Character, World rule, and Note. Artifacts and
conversations are exploratory; only an explicit Story Bible action changes
canon.

Each project also has a lightweight manuscript tree of parts, chapters, and
scenes. Nodes reference artifacts rather than copying their text, so one draft
remains authoritative while manuscript order and status evolve. Compilation is
available as Markdown or DOCX. Artifact working copies autosave separately from
committed revisions; compare and restore operations never rewrite revision
history. Deleting from the writing UI moves artifacts to a reversible trash.

Named project checkpoints contain the complete portable JSON record and are
stored outside the project's governor context with a verified SHA-256 digest.
The ZIP export includes a compiled manuscript, readable conversation/artifact
Markdown, canon, project direction, and the full machine-readable record.
Unified search and character backlinks are computed from existing content and
do not promote, annotate, or otherwise change canon.

Artifacts can be proposed to the existing canon review queue and compared
against accepted canon without model calls or hidden promotion. Manuscript
nodes can start an explicit governed drafting conversation; keeping its output
creates a provenance-linked artifact and links it back to the outline node.

## Household operations

The Compose stack includes a read-only-data backup worker. Backup policy is
configured per workspace, while the deployment supplies one Docker-managed NFS
volume or host-mounted backup root such as `/tank/nfs/marginalia`. Archives
carry file and outer checksums plus build metadata, and every restore test
rebuilds into an isolated temporary root.
Deployments may require a remote filesystem explicitly, preventing a confined
container runtime from silently substituting local disk for the NAS path.
Startup validates durable schemas and applies only supported additive
migrations with a source copy and hash receipt.

See [docs/OPERATIONS.md](docs/OPERATIONS.md) for NAS configuration, health and
version endpoints, exact backup/verification commands, and the no-overwrite
disaster-restore procedure.

## Creative project direction

Each fiction context has one small persisted configuration:

- Project Brief
- Collaborator Stance
- Voice and Style Guidance

It is stored at
`<governor-context-root>/marginalia/project.json`, embeds its owning context ID,
and fails closed if copied into another context. Marginalia injects one
versioned project-context system message into the same message list sent
through `GovernedChatAdapter`; AG governs the resulting provider response in
the normal way. There is no second provider call or prompt stack.

## Qualified AG contract

Marginalia requires Python 3.11 or newer and is pinned to:

- `agent-governor==2.8.1`
- AG commit `e279a94326a0a13dbe43473846b53e4c3a9b31f2`
- published annotated tag `marginalia-chat-contract-m0`
- `receipt-kernel==0.1.0` from that AG checkout
- `receipt-v1==0.1.0` from that AG checkout
- governed-chat daemon contract version `1`

See [AG_CONTRACT.md](AG_CONTRACT.md). `GovernedChatAdapter` refuses to run if
the daemon lacks context-scoped pending state, authoritative receipts, or the
configured state root.

## Reproducible development environment

Expected sibling layout:

```text
git/
├── agent_gov/
└── agent_gov_ui/
    └── marginalia/
```

Create an isolated environment using the same lock as the container:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/pip install --no-deps ../../agent_gov
.venv/bin/pip install --no-deps ../../agent_gov/libs/receipt_kernel
.venv/bin/pip install --no-deps ../../agent_gov/libs/receipt_v1
.venv/bin/pip install --no-deps -e .
```

`requirements.lock` fixes the complete third-party runtime resolution,
`requirements-build.lock` fixes the build backend resolution, and
`AG_CONTRACT_COMMIT` fixes local AG source provenance. This sibling checkout is
a development/build input, not an end-user installation requirement.
The development wheel exposes `marginalia-server`; the end-user `marginalia`
command is always the appliance launcher.

## Runtime and provider ownership

The AG daemon is required. It owns provider credentials, provider selection,
model execution, governance checks, pending state, and authoritative receipts.
Marginalia discovers provider/model information through the daemon; its old
local backend-switch endpoint now rejects requests instead of pretending to
change governed execution.

The image entrypoint starts AG and the web application with one aligned state
root. This direct form is intended for development and diagnostics:

```bash
export MARGINALIA_DATA_ROOT="$PWD/.local-data"
export GOVERNOR_CONTEXT_ID="erin-writing"
export GOVERNOR_MODE="fiction"
export BACKEND_TYPE="codex"           # required supervised AG boundary
export CODEX_PATH="/app/codex-provider.sh"
./entrypoint.sh
```

It executes the equivalent of:

```bash
governor --root "$MARGINALIA_DATA_ROOT" serve --mode fiction --socket "$GOVERNOR_SOCKET"
uvicorn gov_webui.adapter:app --host 0.0.0.0 --port 8000
```

AG's state directory is `$MARGINALIA_DATA_ROOT/.governor`. That exact path is
also the context-manager base used by Marginalia. The launcher rejects
conflicting `GOVERNOR_DAEMON_DIR` or `GOVERNOR_CONTEXTS_DIR` values, refuses
any runtime mode other than `fiction`, and refuses direct AG provider backends
that bypass Marginalia's supervised provider catalog.

## Configured models

An installation may explicitly enumerate existing-command and
OpenAI-compatible models without changing the governed daemon contract. See
[docs/MODEL_PROVIDERS.md](docs/MODEL_PROVIDERS.md) for the generic schema,
selection/provenance semantics, and private deployment boundary.

## Container development

Local container builds use qualified sibling AG sources rather than silently
resolving another implementation:

```bash
./start.sh
```

`sync-deps.sh` verifies AG's current commit and stages its complete Python
distribution plus receipt-kernel and receipt-v1. The Dockerfile installs the
same exact third-party lock used locally and pins Codex CLI `0.146.1`.
`./start-codex.sh`
builds the local image, performs container-owned device login when necessary,
and launches the development stack. Ollama, Claude Code, Kimi Code, OpenAI API,
and Anthropic API selections route through the supervised typed provider
catalog; AG remains authoritative in every case. See
[docs/RELIABILITY.md](docs/RELIABILITY.md) for execution and readiness semantics.

## Verification

```bash
python3 -m pytest -q
python3 -m pytest tests/test_live_governed_chat_contract.py -q -vv
python3 -m ruff check src tests
python3 -m build --no-isolation
```

The live contract test uses a deterministic no-network provider over a real
Unix socket and restarts the daemon between block and resolution. Ordinary CI
does not require an OpenAI, Anthropic, or Ollama credential. The dev lock
includes the exact build backend used by `--no-isolation`.

## Product boundary

Marginalia currently consumes these application-facing AG operations:

- validate daemon contract and state root;
- discover the authoritative provider and models;
- send or stream a governed chat response in one context;
- retrieve pending violation state for that context;
- fix, revise, or proceed against that same pending state;
- retrieve an authoritative receipt and its evidence by stable ID.

The browser does not mint a second governed-execution receipt and does not show
raw hashes as ordinary writing UX. It surfaces governance only when a response
is blocked and the writer must correct it, revise a rule, or allow one explicit
exception. AG evidence remains available behind the application boundary for
correctness and diagnostics.

## Provenance and license

Marginalia preserves the donor repository's Apache-2.0 history and attribution.
See [PROVENANCE.md](PROVENANCE.md), [LICENSE](LICENSE), and [NOTICE](NOTICE).
