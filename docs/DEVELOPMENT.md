# Developing Marginalia

Marginalia is an ag-ng-only fiction application. The runtime path is:

```text
browser -> Marginalia -> GenerationStore -> Docket executor
        -> ag-providerctl -> ag-providerd -> model provider
        -> encrypted candidate -> Marginalia acceptance CAS
```

See [AG_CONTRACT.md](../AG_CONTRACT.md) and
[ARCHITECTURE.md](../ARCHITECTURE.md) for ownership and correctness contracts.

## Exact companion sources

Use checkouts containing the qualified objects:

```text
ag-ng       466dcf2d2dc1ec63ebbde2c7f0b53f2fcf666b95
Docket      181589f910b76030b312d6478bd0ac813a630855
```

The worktrees may be on other branches. `sync-deps.sh` exports the exact pinned
objects and writes identity markers; it never copies the current working tree.
Agent Governor classic is not a source, build, test, or runtime dependency.

## Python verification

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.lock -r requirements-build.lock \
  -r requirements-dev.lock
.venv/bin/pip install --no-build-isolation --no-deps ./receipt-v1
.venv/bin/pip install --no-build-isolation --no-deps -e .

MARGINALIA_AG_NG_SOURCE_DIR=/path/to/ag-ng \
MARGINALIA_DOCKET_SOURCE_DIR=/path/to/docket \
  .venv/bin/pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
node tools/check-browser-startup.js
npm ci && npm run test:browser
```

The active release suite excludes the audited classic and donor-product modules
listed in `tests/conftest.py`; they remain in Git for archaeology. The exact
collection arithmetic, disposition of every retired module, and replacement
coverage for writer workflows and migration invariants are maintained in
[`ag-ng-migration/TEST-COVERAGE-CROSSWALK.md`](ag-ng-migration/TEST-COVERAGE-CROSSWALK.md).
New product behavior must be covered by the ag-ng-only suite, not by installing
classic to satisfy old fixtures.

## Container development

Copy `.env.example` to the ignored `.env`, create the NAS-backed provider
catalog/secrets/config described in [MODEL_PROVIDERS.md](MODEL_PROVIDERS.md),
then run:

```bash
MARGINALIA_AG_NG_SOURCE_DIR=/path/to/ag-ng \
MARGINALIA_DOCKET_SOURCE_DIR=/path/to/docket \
  ./start.sh
```

For a Codex command provider, `./start-codex.sh` builds every service from the
same image and performs device login in ag-providerd's isolated auth volume.
Use the Claude or Ollama Compose overlay only when the matching catalog route is
configured.

The host cannot substitute a direct `uvicorn` or old single-container launcher
for qualification: those omit ag-ng provider authorization and Docket custody.

## Candidate discipline

Freeze and commit a candidate before building it. Build from that exact commit,
record the image digest, qualify that digest, and deploy only that digest after
separate production approval. Any repair creates a new candidate and invalidates
affected validation.
