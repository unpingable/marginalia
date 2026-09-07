# Gate 3C executor and worker checkpoint

Status: executor mechanics and the exact companion process composition pass.
Deployment wiring is intentionally not enabled yet.

## Frozen companion identities

- ag-ng commit: `cb85d363e2495a75f78c28fb8ce9b46af1f289c0`
- Docket commit: `c49ad8d0f26fb2a13b9dbafdde84d7abfe1f867b`
- Qualified VM `ag-loopctl` SHA-256:
  `aa72a2381e955cb3c310273c459bfdba6440cd8d3e326d85b3924f10f199c524`
- Qualified VM `docket` SHA-256:
  `d1067a625c7080f66a7e385ace4566d2a3acfca199ca229c7528adf46a87e528`

## Established behavior

- The worker creates one exact, occurrence-bound executor plan and a typed
  ag-ng catalog entry for each logical generation.
- ag-ng owns authorization, Docket owns attempt custody, the Marginalia
  executor owns provider dispatch/evidence, and the application remains the
  only authority that can accept a candidate into a writing session.
- The provider boundary is durably marked before Docket's dispatch command.
  A replacement worker at `authorization_consumed` therefore invokes recovery,
  never another dispatch. Worker recovery does not claim that a lost provider
  HTTP execution can resume.
- Every controller invocation records immutable argv, return code, stdout, and
  stderr. A restarted worker resumes the next log sequence instead of replacing
  an earlier command record.
- Exact response bodies use the encrypted evidence store specified in
  `EVIDENCE-SECURITY.md`; the executor plan binds its data path, separately
  supplied keyring, and retention.
- Resolver inputs and executor dispatches are closed-shape and capped at 1 MiB.
- The AG signing key is validated as ring's Ed25519 PKCS#8 v2 form. Marginalia
  independently derives the public key from the seed and compares it with the
  embedded public component before creating Docket trust.

## Tests

The focused unit set has 22 passing cases. The opt-in process witness invokes
the two qualified Rust binaries, the real Marginalia resolvers, generation
store, encrypted evidence store, and executor core. It substitutes only the
external model call with a deterministic response. The witness dispatched
once, reached `settled_observation_required`, retained a candidate, reopened
the authoritative state, and did not create a second dispatch.

The exact process witness is enabled with:

```text
MARGINALIA_TEST_AG_LOOPCTL=/path/to/exact/ag-loopctl
MARGINALIA_TEST_DOCKET=/path/to/exact/docket
python3 -m pytest -q tests/test_generation_companion_processes.py
```

The test skips when exact binary paths are not supplied; Gate 3H must supply
them and may not count a skip as companion qualification.

## Resume

Package only these exact companion commits, then implement the application-side
cross-process project lock and revision/canon/guidance acceptance CAS. Production
deployment remains unauthorized.
