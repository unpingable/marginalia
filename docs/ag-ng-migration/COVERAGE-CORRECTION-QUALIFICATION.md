# Coverage and generation-control correction qualification

Date: 2026-09-08

## Superseding candidate

- Marginalia source: `718ac4549a99b24bd935432ecf44eda45acac9f3`
- ag-ng provider ingress: `c3210f156208b22bf21e7bd1910a84a85b519538`
- Docket executor host: `181589f910b76030b312d6478bd0ac813a630855`
- local image: `marginalia:ag-ng-718ac45`
- image ID: `sha256:2a958bd3f9c3b64aeb003086f28f6869a69ea1ae1ee27dd683b97645d1888289`
- image `ag-loopctl` SHA-256:
  `da82f3e236e116cfab56809a19ce811fd9c421e0e98d8072e79b6c37e1d21a07`
- image `docket` SHA-256:
  `21e07e8b3fe143e7e13b0ba0aaf8c6c363539646271193ffa368cd7fbe0c2c2b`

The first correction relabeled the project admission control as **Generation
enabled**, **Generation paused**, or **Generation unavailable**. Paused
continues to stop only new dispatches; inspection, reconciliation, evidence
recovery, and historical acceptance replay remain available. No classic or
synchronous alternate path was added.

The semantic follow-up reviewed every behavior family in all ten excluded
modules. It repaired the fiction-product route boundary for the deliberately
preserved read-only `receipt_v1` archive and made export and verification
discoverable through `/api/info`. Historical receipts remain evidence only and
cannot authorize ag-ng work. It also ported missing configured-auth, session,
synthetic-isolation, and artifact HTTP-negative cases into active product tests.

The remainder of the correction is tests, CI, and documentation. The ag-ng and
Docket commits and binaries are identical to the original Gate 6 candidate, so
the provider-dispatch, custody, crash, revision-CAS, compatibility, and evidence
restore results in `GATE6-QUALIFICATION.md` remain evidence for unchanged
runtime modules. The affected application, browser, image, and exact-companion
surfaces were requalified against the frozen correction commit.

## Results

- Ruff check and format check: pass.
- ordinary host suite: 460 collected; 459 passed and the one exact-companion
  test conditionally skipped because no executable paths were supplied.
- complete host suite with `ag-loopctl` and `docket` extracted from the exact
  candidate image: **460 passed, 0 skipped**. The cross-process custody witness
  executed within that run.
- targeted product and historical-receipt suite: **76 passed**.
- Playwright reliability suite: **4 passed**, including visible unavailable,
  paused, and enabled labels, saved-state reload, lost-acknowledgement recovery,
  usage/cost display, and rapid double-submit protection.
- candidate image smoke: exact source/ag-ng/Docket labels passed; classic
  distributions absent; installed UI contains the three writer-facing labels
  and no `ag-ng ·` status label.

The exact 839-to-449 initial migration arithmetic remains recorded in
`TEST-COVERAGE-CROSSWALK.md`, but was not used as semantic acceptance. That
crosswalk now records every excluded behavior family, its executable
replacement or retirement reason, and any lost negative case. CI extracts the
exact companions from the built image and runs the custody witness as a
mandatory post-build step.

## Decision

This candidate supersedes `cf39bf3339ba958675248814d774ea9a2b147f88`
for any future production decision. Production was not changed. Credential
rotation and every precondition in `PRODUCTION-DEPLOYMENT-PACKET.md` remain
mandatory.
