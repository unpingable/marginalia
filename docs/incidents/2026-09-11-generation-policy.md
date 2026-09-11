# Generation policy incident — 2026-09-11

Status: service restored and product repair deployed; writer browser acceptance pending.

## Scope and symptom

The Doverton project (`fc3b21be00c3`) returned `GENERATION FAILED` with the
message that durable generation was off and synchronous generation was not
available. This incident was independent of Orion/CUDA work. Orion was neither
enabled nor exercised during diagnosis, recovery, qualification, or deployment.

The project retained a legacy `durable=false` setting from
`2026-09-08T07:31:52.698932+00:00`. Removal of synchronous generation changed
the effective meaning of that old preview opt-in: the false value became a
total generation pause. The UI nevertheless allowed submission, so the policy
failure appeared only after Erin submitted a turn.

Read-only diagnosis established that the global maintenance switch was not
active, Doverton had no pending logical generation request or Docket work to
recover, and a non-Orion production route was ready. No prompt, response,
credential, or other raw user content was printed during diagnosis.

## Incident recovery transition

The then-supported settings endpoint did not accept an expected revision and
therefore could not protect a concurrent writer. Recovery used one direct
SQLite compare-and-set transaction as a narrowly bounded incident exception.
It had all of these preconditions:

- database/project identity: Doverton, `fc3b21be00c3`;
- `dispatch_enabled = 0`;
- `updated_at = 2026-09-08T07:31:52.698932+00:00`;
- transaction mode: `BEGIN IMMEDIATE`;
- the update was required to affect exactly one row, otherwise the transaction
  would not be accepted.

The exact parameterized mutation was:

```sql
UPDATE generation_settings
SET dispatch_enabled = 1, updated_at = ?
WHERE project_id = ? AND dispatch_enabled = 0 AND updated_at = ?;
```

Its parameters supplied the new timestamp
`2026-09-11T15:40:51.605037+00:00`, project `fc3b21be00c3`, and the old
timestamp above, in that order. The fallback value remained exactly `[]`.
No session, message, request, dispatch, candidate, or evidence row was edited or
deleted. The direct-storage path is not the normal operational procedure.

Commit `d420ba47806bd9adc1b7c494b58a4c3e1543e031` subsequently added a
revision-checked settings endpoint and client (`expected_version`) so future
changes fail closed on a state-transition race and do not require this
exception.

## Product repair and migration disposition

- The setting is now **Generation enabled**, with **Generation paused** as its
  disabled state.
- The composer blocks submission while the project is paused and exposes an
  actionable control to open the authorized setting.
- Project pause, global operator pause, provider unavailability, and provider
  execution failure have distinct failures.
- There is no synchronous fallback.
- Existing legacy generation-policy rows are migrated to enabled with an
  explicit migration disposition; a legacy durable false value cannot silently
  become a total product disablement.
- Frontend controls and error states are treated as part of the backend policy
  contract and covered by API/product and browser regressions.

## Qualification and deployment evidence

- Deterministic suite: 492 passed, 2 environment-gated exact-binary witnesses
  skipped in the ordinary invocation. The skips were precisely
  `tests/test_generation_companion_processes.py::test_exact_companions_dispatch_once_and_reopen_settled_state`
  (exact `ag-loopctl` and Docket paths absent) and
  `tests/test_provider_policy_companion_processes.py::test_exact_daemon_loads_and_providerctl_routes_production_shaped_catalog`
  (exact provider binaries and a root-owned fixture absent).
- The ag-ng/Docket dispatch-once and reopen-settled-state witness was rerun with
  the deployed image binaries: 1 passed in 1.64 seconds.
- The `ag-providerd`/`ag-providerctl` production-shaped policy witness was
  rerun in an ephemeral root-owned, network-disabled container: 1 passed in
  0.90 seconds.
- Both witnesses are mandatory, individually bounded steps in the `container`
  GitHub CI job; the central custody witness cannot be omitted by the ordinary
  suite's environment gate.
- Focused Python 3.11 suite: 100 passed.
- Browser suite: 5 passed.
- Production synthetic: PASS through the real serialized non-Orion path in
  12.833 seconds; isolated synthetic context, with no writer-session pollution.
- Deployed image: `marginalia:generation-policy-d420ba4-candidate`, image ID
  `sha256:6a7c32627cb24ab1b4526d933def536b8a712e7fffdd9bb9b3b197bb83a2ab63`.
- Rollback image: `marginalia:rollback-generation-policy-20260911-9ea6093`,
  image ID
  `sha256:7fbd4070cb6a4217525599bc6d4203fff2aecd76d00d01aa60ec42b67d0a9047`.
- Deployment preserved 7 writer sessions and 747 messages; source copy
  validation covered 983 files without source digest change.

## Remaining incident acceptance

Infrastructure qualification is not a substitute for Erin's browser/project
workflow. Before this incident is closed, Erin must refresh Marginalia, open
Doverton, select her chosen non-Orion model, and complete one ordinary turn.
Acceptance requires the turn to become visibly successful through her normal
browser workflow, with no project-pause, provider-unavailable, or execution
failure state.
