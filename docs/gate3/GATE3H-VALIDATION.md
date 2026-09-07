# Gate 3H test-instance acceptance

Gate 3B through Gate 3H passed test-instance qualification on `2026-09-07`.
Production deployment remains unauthorized and was not performed.

## Frozen candidate

- Marginalia commit: `109a0ef8c612f42f83be5376f6f585350e8ea796`
- Marginalia tree: `fa6597e8789951f5b26e21531001010ff3a73f26`
- Image: `marginalia:gate3-109a0ef`
- Local image ID: `sha256:048973fba14e25915d8170f336bf87e3949ecc58c8d34e0329c34c4c5f3c0fe9`
- ag-ng commit: `cb85d363e2495a75f78c28fb8ce9b46af1f289c0`
- Docket commit: `c49ad8d0f26fb2a13b9dbafdde84d7abfe1f867b`
- Agent Governor commit: `e279a94326a0a13dbe43473846b53e4c3a9b31f2`

The image labels contain all three application/companion identities. The image
imports Marginalia and starts the packaged `ag-loopctl`, `docket`, and Codex CLI
successfully. It was built only after the candidate commit was frozen and
pushed. This record is a later documentation-only commit and is not part of the
tested image.

## Qualification results

The disposable KVM environment, toolchain, dependency locks, user-namespace
probe, companion results, and Docket failure investigation are specified in
`GATE3A-QUALIFICATION.md`. For the final candidate, the independently retained
VM result records:

- real Bubblewrap/user-namespace probe: passed;
- Ruff lint and format: passed;
- complete Marginalia suite: 839 passed, 0 failed, 0 skipped;
- exact ag-ng/Docket cross-process witness, run separately: 1 passed;
- completed at `2026-09-07T17:11:55+00:00`.

The final GitHub Actions run passed the quality, test, browser, and exact-source
container jobs: <https://github.com/unpingable/marginalia/actions/runs/34146478071>.
The browser job uses locked Playwright 1.63.0 and verifies that Erin can find
the Generation reliability controls, see an unavailable state, enable durable
custody, choose the confirmed-failure fallback, and save the settings.

Two full-suite VM failures were retained during final qualification. Both
provider-tree tests used a 200 ms process deadline and then assumed their
fixture interpreter had already written PID files. Under load, the deadline
could expire before interpreter startup, so the tests had not necessarily
exercised a hung provider tree. Commits `638b278` and `109a0ef` changed the test
deadline to two seconds, retained the 60-second fixture wedge, and explicitly
require proof that the provider and child started. Both corrected witnesses
passed together in five consecutive VM repetitions before the final complete
suite passed. No product timeout was enlarged and no test was excluded.

## Test-instance transitions

The final image is running only on the isolated loopback test instance at
`127.0.0.1:18084`, with an isolated data volume, runtime volume, deterministic
local provider, and separately mounted test keys.

- A fresh final-candidate request, `gen_5de52fc66ada40e891a56c3c14adf845`,
  reached `authored`, inserted one user/assistant pair, and advanced its session
  from revision 0 to revision 1 with one provider invocation.
- The final candidate reopened the killed-provider request
  `gen_94e866c997c44201b3c28bf36b07b854` as `unknown`; replacement-worker
  reconciliation did not increase the provider invocation count and its
  session remained at revision 0 with no messages.
- Turning durable generation off left that request inspectable and
  reconcilable and did not dispatch it. The setting was restored afterward.
- The final candidate reopened request
  `gen_61d0105bdca846e29fd6c4d2c403613a` as blocked with `accepted canon changed`;
  the associated session remained at revision 0 with no messages.

The SIGKILL and in-flight canon mutation were injected against candidate
`fee483b`, immediately before the final two commits. Those commits modify only
CI checkout wiring and reliability tests; application and container runtime
sources are unchanged. The final exact image then performed the authoritative
reopen/reconciliation checks above.

Previous-image read/write compatibility, candidate restart, encrypted evidence
restore with the separately supplied recovery key, and the retained-backup
expiry limitation were validated before this final CI-only delta. Their runtime
implementation is unchanged; operational and security requirements remain in
`DURABLE-GENERATION-OPERATIONS.md` and `EVIDENCE-SECURITY.md`.

## Release gate

Production containers `marginalia`, `marginalia-synthetic`, and
`marginalia-backup` remain on `marginalia:gate1-2ecae39`. Production promotion
requires a separate acceptance decision. If any executable repair is made, this
candidate's affected validation must be repeated and a new exact image built.
