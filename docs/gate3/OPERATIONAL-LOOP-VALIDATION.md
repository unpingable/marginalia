# Operational-loop candidate validation

The bounded key-recovery, production-onboarding, impatient-writer, and passive
telemetry campaign passed test-instance qualification on `2026-09-07`.
Production promotion remains a separate acceptance decision.

## Exact candidate

- Marginalia commit: `58decafc9a55a231041abbf01d165f357a0e78af`
- Marginalia tree: `541c97f062a3f88764f7dac7cf1020c0877912b3`
- Image: `marginalia:opsloop-58decaf`
- Local image ID: `sha256:e863c7a99e1aa97c92bc33ce3634ac4ed5af2dab429da64c3b3e6c7c2dadd1e8`
- Agent Governor commit: `e279a94326a0a13dbe43473846b53e4c3a9b31f2`
- ag-ng commit: `cb85d363e2495a75f78c28fb8ce9b46af1f289c0`
- Docket commit: `c49ad8d0f26fb2a13b9dbafdde84d7abfe1f867b`

The image labels contain the application, ag-ng, and Docket identities. The
image imported Marginalia and started the packaged `ag-loopctl`, `docket`, and
Codex CLI successfully. It was built from the clean candidate after that commit
was frozen and pushed. This file is a later documentation-only record and is
not part of the tested image.

## Validation

- Complete exact-companion Marginalia suite: 846 passed, 1 skipped.
- Focused repaired-area suite: 90 passed.
- Browser startup check: passed.
- Playwright reliability suite: 4 passed, covering discoverable controls,
  toggle/fallback, reload and lost acknowledgement, usage/cost presentation,
  and rapid double-submit behavior.
- Python source distribution and wheel: built successfully.
- GitHub Actions quality, test, browser, and exact-source container jobs:
  <https://github.com/unpingable/marginalia/actions/runs/34155586042>.

On the isolated instance, a fresh durable request reached `authored`, inserted
one user/assistant pair, and exposed the actual model, normalized reported
usage, and an explicit unavailable-cost state. Exact replay returned the same
historical result after the dispatch switch was disabled. A new request while
disabled returned HTTP 409 and did not fall through to synchronous generation.
Two rapid deliveries from the same session revision produced exactly one
accepted pair; the other was blocked on revision change.

The first isolated fixture attempt was intentionally retained as `unknown`.
The candidate was correct: its provider fixture could not append to an
invocation ledger because the test bind was read-only. Recreating only the
disposable containers with that fixture ledger writable produced one provider
invocation and a successful acceptance. No application repair or timeout
increase resulted from that environment failure.

## Rollback compatibility

The currently deployed image `marginalia:gate3-109a0ef` opened candidate-upgraded
state, read an accepted candidate session, created a new session, and rewrote
the accepted session. The replacement candidate then reopened both writes.

That exercise found one compatibility edge: the previous image does not know
the new derived per-message `accounting` object and removes it when rewriting a
session. Candidate commit `58decaf` repairs forward recovery from the retained
provider, model, and normalized usage facts. It labels historical cost
unavailable instead of recomputing it from possibly changed rates. The repair
was followed by a complete suite, new image build, CI pass, rollback round trip,
and fresh candidate request.

## Key recovery

The production signing and evidence recovery set is outside Git on the NFS NAS
at the owner-only location documented in `NAS-KEY-RECOVERY.md`. A harmless
fixture encrypted with the deployed production evidence key was restored and
decrypted using only the NAS keyring mounted read-only. The NAS is a separate
host failure domain, not a geographically off-site vault.

## Release gate

Erin's production web, generation, backup, and synthetic containers remain on
`marginalia:gate3-109a0ef`. Promotion must use the exact candidate image ID
above with Compose builds disabled. Any executable repair creates a new
candidate and invalidates the affected validation.
