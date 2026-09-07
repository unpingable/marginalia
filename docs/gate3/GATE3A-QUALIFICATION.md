# Gate 3A qualification

Gate 3A passed on `2026-09-07`. The exact pinned ag-ng and Docket sources compose
without a source patch. No test was excluded, and no baseline deviation was made.

## Qualified inputs

- ag-ng commit `cb85d363e2495a75f78c28fb8ce9b46af1f289c0`, tree
  `e16263115338369e5763b46f5b36cecaeb1b4466`, Cargo.lock SHA-256
  `2f266bd3d0275bac448daf3f6f8748028fa2ea05df73e19f2c4b478c292864a7`.
- Docket commit `c49ad8d0f26fb2a13b9dbafdde84d7abfe1f867b`, tree
  `19573c62de34efc4651b7af52bb9315339255194`, Cargo.lock SHA-256
  `c8753bcca410fbbf6617f630e4d439b1db929c54829aeff7591b5216fbe39ed2`.
- Ubuntu 24.04.4 LTS cloud image SHA-256
  `d0fe84bb5f80853425fa6be28e2c106f30104c3cfe8611933f2e65c9b63f0e30`.
- QEMU 8.2.2 executable SHA-256
  `8a35ccba41582fc6c38b9df85fc9e35fa1d42f414d2d7d8090ee9b2f5e7c0854`.

The disposable KVM guest had four vCPUs and 5.8 GiB RAM and ran Linux
6.8.0-138-generic. Rust was 1.94.0 (`4a4ef493e3a1488c6e321570238084b38948f6db`),
Cargo was 1.94.0 (`85eff7c80277b57f78b11e28d14154ab12fcf643`), Git was
2.43.0, SQLite was 3.45.1, and Bubblewrap was Ubuntu package
`0.9.0-1ubuntu0.1`. Cargo metadata for both repositories is retained in the
host-copied evidence set.

## Namespace qualification

Inside the guest, `kernel.unprivileged_userns_clone=1` and
`user.max_user_namespaces=23548`. Ubuntu's restricted-userns policy initially
assigned `/usr/bin/bwrap` to a profile that denied the required namespace
operation. An executable-specific AppArmor profile, derived from ag-ng commit
`746d3919`, was installed only in the disposable guest. Its SHA-256 is
`dc79c682840565e473b7c478eb8acdcd53579efd9d76bab5164740f01045f57c`.
The real Bubblewrap probe and all worker tests then passed. Production-host
namespace and AppArmor settings were not changed.

## Results

- Docket `cargo test --workspace --all-targets`: 279 passed, 0 failed.
- ag-ng `cargo test --workspace --all-targets`: 498 passed, 0 failed, 3 ignored.
- Required ignored cross-process witness
  `signed_issuance_crosses_docket_and_effectd_once_then_settles`: 1 passed.
- Five repetitions of both complete suites running concurrently: each Docket
  run had 279 passed and 0 failed; each ag-ng run had 498 passed, 0 failed, and
  3 ignored.
- Durable producer invocation `01975bbc6b1844019f71270f5330880d`
  completed at `2026-09-07T13:49:01+00:00` with `result=pass`.

Worker recovery in this qualification establishes recovery of custody and
evidence only. It does not establish that an interrupted external HTTP provider
execution can be resumed.

## Docket failure investigation

The original host full-suite failure is preserved verbatim in
`DOCKET-FIRST-FAILURE.txt`. The failing test was
`custody_boundary_specimen_shows_premise_and_conflicting_evidence` in
`crates/gwr-local/tests/dossier.rs`; it expected an indeterminate dispatch
outcome. Its later isolated pass was treated only as diagnostic evidence.

The full suite passed in the qualification guest and then passed five more
times under the original suite condition, concurrently with the complete ag-ng
suite. The failure did not reproduce. The test's own dossier root includes the
test name and process ID, but broker helpers also use process-global
`/tmp/gwr-index-{dispatch}` and `/tmp/gwr-patch-{dispatch}` paths. Dispatch IDs
are seeded from current nanoseconds and process ID. Those are relevant shared
resources and a plausible collision mechanism, but the evidence does not prove
that mechanism caused the original result. Changing Docket product code without
a reproduction would be an unjustified companion baseline deviation, so no
patch was made and no test was excluded.

## Evidence custody

The full local evidence set is retained under the ignored directory
`.gate3/qualification-vm/marginalia-gate3a-20260907-001/results-final/`.
`GATE3A-EVIDENCE.sha256` records the digest of every copied result file. The VM
checkpoint, source bundles, cloud-init seed, run-specific SSH identity, serial
log, and overlay remain in the run directory until Gate 3 implementation
checkpoints no longer require the disposable guest.
