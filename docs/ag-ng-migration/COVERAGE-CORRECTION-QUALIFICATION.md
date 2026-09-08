# Coverage and generation-control correction qualification

Date: 2026-09-08

## Superseding candidate

- Marginalia source: `cf39bf3339ba958675248814d774ea9a2b147f88`
- ag-ng provider ingress: `c3210f156208b22bf21e7bd1910a84a85b519538`
- Docket executor host: `181589f910b76030b312d6478bd0ac813a630855`
- local image: `marginalia:ag-ng-cf39bf3`
- image ID: `sha256:0753cde148bf02e301cdaa2b7d489d8fb141d912c30347cf3a1abc4563a2641c`
- build time label: `2026-09-08T06:44:32Z`

The correction changes the shipped runtime only in the static writer UI. It
relabels the project admission control as **Generation enabled**, **Generation
paused**, or **Generation unavailable**. Paused continues to stop only new
dispatches; inspection, reconciliation, evidence recovery, and historical
acceptance replay remain available. No classic or synchronous alternate path
was added.

The remainder of the correction is tests, CI, and documentation. The ag-ng and
Docket commits and binaries are identical to the original Gate 6 candidate, so
the provider-dispatch, custody, crash, revision-CAS, compatibility, and evidence
restore results in `GATE6-QUALIFICATION.md` remain evidence for unchanged
runtime modules. The affected application, browser, image, and exact-companion
surfaces were requalified against the frozen correction commit.

## Results

- Ruff check and format check: pass.
- ordinary host suite: 450 collected; 449 passed and the one exact-companion
  test conditionally skipped because no executable paths were supplied.
- complete host suite with the exact candidate-image `ag-loopctl` and `docket`
  binaries supplied: **450 passed, 0 skipped**.
- exact-companion custody witness, repeated after extracting both binaries from
  the new image: **1 passed**.
- Playwright reliability suite: **4 passed**, including visible unavailable,
  paused, and enabled labels, saved-state reload, lost-acknowledgement recovery,
  usage/cost display, and rapid double-submit protection.
- candidate image smoke: exact source/ag-ng/Docket labels passed; classic
  distributions absent; installed UI contains the three writer-facing labels
  and no `ag-ng ·` status label.

The exact 839-to-449 migration arithmetic, all 418 retired-module cases, five
direct contract replacements, and active writer/migration invariant coverage
are recorded in `TEST-COVERAGE-CROSSWALK.md`. CI now extracts the exact
companions from the built image and runs the custody witness as a mandatory
post-build step.

## Decision

This candidate supersedes `8d672105d0c179120ba516f9df7254f0fa2a2cbd`
for any future production decision. Production was not changed. Credential
rotation and every precondition in `PRODUCTION-DEPLOYMENT-PACKET.md` remain
mandatory.
