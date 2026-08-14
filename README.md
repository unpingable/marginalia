# Marginalia

Marginalia is a standalone governed creative-writing application. Agent
Governor (AG) is its direct execution and governance substrate; Constellation
is not part of its runtime.

```text
Marginalia → GovernedChatAdapter → Agent Governor daemon → model provider
```

No NQ, Nightshift, Monitor, Maude, Desk, qualification service, or Phosphor
control-plane service is required.

## M0 status

This repository was extracted from the last useful pre-Desk governed-chat
state of `gov-webui` at commit
`b0c99e417363f216fd53490a31a8e6f5f485a92b`. The internal Python package is
still named `gov_webui` to keep the correctness slice reviewable; the
distribution, command, repository, UI title, runtime, and container are named
Marginalia.

M0 establishes and tests one trustworthy invariant:

> A governed response can be blocked in one chat context, remain durably
> pending through a daemon restart, be observed and resolved only in that
> context, and retain authoritative AG receipt linkage.

The donor UI still contains legacy code/research/operator surfaces. They are
not the Marginalia product direction and will be removed or simplified in a
later product-focused slice. They are not dependencies of governed chat.

## Qualified AG contract

Marginalia requires Python 3.11 or newer and is pinned to:

- `agent-governor==2.8.1`
- AG commit `e279a94326a0a13dbe43473846b53e4c3a9b31f2`
- `receipt-v1==0.1.0` from that AG checkout
- governed-chat daemon contract version `1`

See [AG_CONTRACT.md](AG_CONTRACT.md). `GovernedChatAdapter` refuses to run if
the daemon lacks context-scoped pending state, authoritative receipts, or the
configured state root.

## Reproducible local environment

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
.venv/bin/pip install --no-deps ../../agent_gov/libs/receipt_v1
.venv/bin/pip install --no-deps -e .
```

`requirements.lock` fixes the complete third-party runtime resolution,
`requirements-build.lock` fixes the build backend resolution, and
`AG_CONTRACT_COMMIT` fixes local AG source provenance.

## Runtime and provider ownership

The AG daemon is required. It owns provider credentials, provider selection,
model execution, governance checks, pending state, and authoritative receipts.
Marginalia discovers provider/model information through the daemon; its old
local backend-switch endpoint now rejects requests instead of pretending to
change governed execution.

The supported single-process launcher starts AG and the web application with
one aligned state root:

```bash
export MARGINALIA_DATA_ROOT="$PWD/.local-data"
export GOVERNOR_CONTEXT_ID="erin-writing"
export GOVERNOR_MODE="fiction"
export BACKEND_TYPE="anthropic"       # read by AG, not by the web UI
export ANTHROPIC_API_KEY="..."        # or configure another AG backend
./entrypoint.sh
```

It executes the equivalent of:

```bash
governor --root "$MARGINALIA_DATA_ROOT" serve --mode fiction --socket "$GOVERNOR_SOCKET"
uvicorn gov_webui.adapter:app --host 0.0.0.0 --port 8000
```

AG's state directory is `$MARGINALIA_DATA_ROOT/.governor`. That exact path is
also the context-manager base used by Marginalia. The launcher rejects
conflicting `GOVERNOR_DAEMON_DIR` or `GOVERNOR_CONTEXTS_DIR` values.

## Container

Container builds use qualified local AG sources rather than silently resolving
another implementation:

```bash
./start.sh
```

`sync-deps.sh` verifies AG's current commit, stages AG and receipt-v1, and the
Dockerfile installs the same exact third-party lock used locally. Optional
provider overrides are available for Ollama, Claude Code, and Codex; in every
case the provider remains configured and invoked by AG.

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
raw hashes as ordinary writing UX. AG evidence remains available behind the
application boundary for correctness and diagnostics.

## Provenance and license

Marginalia preserves the donor repository's Apache-2.0 history and attribution.
See [PROVENANCE.md](PROVENANCE.md), [LICENSE](LICENSE), and [NOTICE](NOTICE).
