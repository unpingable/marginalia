# Developing Marginalia

This document collects contributor setup, runtime architecture, contract pins,
verification, and repository history. The writer-facing overview lives in the
[root README](../README.md).

## Architecture and Agent Governor contract

Marginalia is a fiction-only application over Agent Governor (AG):

```text
browser/API -> Marginalia -> GovernedChatAdapter -> Agent Governor daemon
            -> supervised provider catalog -> configured model
```

AG owns governed execution, context-scoped pending state, and authority
receipts. Marginalia owns projects, conversations, artifacts, canon review,
manuscript organization, backups, and the typed authored/operational boundary.
The browser does not mint receipts or treat infrastructure status as prose.

The qualified dependency contract is:

- Python 3.11 or newer;
- `agent-governor==2.8.1`;
- AG commit `e279a94326a0a13dbe43473846b53e4c3a9b31f2`;
- annotated tag `marginalia-chat-contract-m0`;
- `receipt-kernel==0.1.0`;
- `receipt-v1==0.1.0`;
- governed-chat daemon contract version 1.

See [AG_CONTRACT.md](../AG_CONTRACT.md), [ARCHITECTURE.md](../ARCHITECTURE.md),
[API.md](API.md), and [RELIABILITY.md](RELIABILITY.md) for the exact contracts.

## Repository history and product boundary

This repository was extracted from the last useful pre-Desk governed-chat
state of `gov-webui` at commit
`b0c99e417363f216fd53490a31a8e6f5f485a92b`. The internal Python package
remains `gov_webui` to keep the correctness slice reviewable; the product,
distribution, command, runtime, and container are Marginalia.

Historical donor endpoints and tests remain temporarily in `adapter.py`.
They are unreachable by default and absent from `/api/info`.
`MARGINALIA_ENABLE_DONOR_ROUTES=1` exists only for the retained compatibility
suite and is not a supported product mode. NQ, Nightshift, Monitor, Maude,
Desk, qualification services, and Phosphor are not runtime dependencies.

For donor history and attribution, see [PROVENANCE.md](../PROVENANCE.md).

## Local Python environment

Expected sibling layout:

```text
git/
├── agent_gov/
└── agent_gov_ui/
    └── marginalia/
```

Create an isolated environment using the same locks as the container:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.lock -r requirements-build.lock \
  -r requirements-dev.lock
.venv/bin/pip install --no-build-isolation --no-deps ../../agent_gov
.venv/bin/pip install --no-build-isolation --no-deps \
  ../../agent_gov/libs/receipt_kernel
.venv/bin/pip install --no-build-isolation --no-deps \
  ../../agent_gov/libs/receipt_v1
.venv/bin/pip install --no-build-isolation --no-deps -e .
```

`requirements.lock` fixes third-party runtime resolution,
`requirements-build.lock` fixes the build backend, and
`AG_CONTRACT_COMMIT` fixes local AG source provenance. The sibling checkout is
a development/build input, not an end-user prerequisite.

## Direct runtime

The image entrypoint starts AG and Marginalia with one aligned state root. For
development and diagnostics:

```bash
export MARGINALIA_DATA_ROOT="$PWD/.local-data"
export GOVERNOR_CONTEXT_ID="erin-writing"
export GOVERNOR_MODE="fiction"
export BACKEND_TYPE="codex"
export CODEX_PATH="/app/codex-provider.sh"
./entrypoint.sh
```

This is equivalent to starting AG under `$MARGINALIA_DATA_ROOT/.governor`
and then serving `gov_webui.adapter:app`. The entrypoint rejects split state
roots, non-fiction mode, and direct legacy AG backends that bypass
Marginalia's supervised provider lifecycle.

Configured models, provider credentials, and local-command adapters are
documented in [MODEL_PROVIDERS.md](MODEL_PROVIDERS.md).

## Container development

Local image builds use qualified sibling sources:

```bash
./start.sh
```

`sync-deps.sh` verifies the AG commit and stages AG, receipt-kernel, and
receipt-v1. The Dockerfile installs the exact locks and pins Codex CLI
`0.146.1`. `./start-codex.sh` builds the local image, handles
container-owned device login when needed, and launches the development stack.

See [DISTRIBUTION.md](DISTRIBUTION.md) for release-image construction and
clean-machine qualification, and [OPERATIONS.md](OPERATIONS.md) for durable
state, backups, health, and restore procedures.

## Verification

```bash
python3 -m pytest -q
python3 -m pytest tests/test_live_governed_chat_contract.py -q -vv
python3 -m ruff check src tests
python3 -m ruff format --check src tests
python3 -m build --no-isolation
```

The live contract test uses a deterministic no-network provider over a real
Unix socket and restarts AG between block and resolution. Ordinary CI checks
lint, format, browser JavaScript syntax, and package build; checks out the exact
AG contract commit; runs the deterministic suite; builds the production image;
and performs import/Codex smokes.

## Browser qualification debt

The existing Playwright package and `tools/screenshots` harness capture seeded
screenshots; they are not a functional generation-boundary E2E suite and are
not provisioned in CI. A future focused target should run an isolated candidate
stack with a test-only deterministic fault provider:

```bash
npm install
npx playwright install chromium
npx playwright test --config=tests/e2e/playwright.config.ts \
  tests/e2e/generation-failure.spec.ts
```

That test/config do not exist yet. They should prove: an existing successful
passage remains visible; an injected timeout renders a non-narrative failure
card with no fork/select/keep controls; retry uses the preserved prompt; and
exactly one authored turn becomes durable. The missing pieces are a
production-boundary fault fixture, isolated stack lifecycle, and multi-tab
helpers. Keep those hooks test-only and require explicit fixture configuration. The broader
stateful-fuzzing plan is in [SYNTHETIC_QUALIFICATION.md](SYNTHETIC_QUALIFICATION.md).

The frozen qualified baseline, priority backlog, and cold-start checklist are in
[NEXT_WORK.md](NEXT_WORK.md).

## License

Marginalia preserves the donor repository's Apache-2.0 history and attribution.
See [LICENSE](../LICENSE), [NOTICE](../NOTICE), and
[PROVENANCE.md](../PROVENANCE.md).
