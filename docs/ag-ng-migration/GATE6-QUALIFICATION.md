# Full ag-ng migration — Gate 6 qualification

Date: 2026-09-08

## Qualified candidate

- Marginalia source: `8d672105d0c179120ba516f9df7254f0fa2a2cbd`
- ag-ng provider ingress: `c3210f156208b22bf21e7bd1910a84a85b519538`
- Docket executor host: `181589f910b76030b312d6478bd0ac813a630855`
- local image: `marginalia:ag-ng-8d67210`
- image ID: `sha256:42da34573d510ff1e00572aff4026486cbbf2b0a87adc0b82aac2121c7ab26b4`
- build time label: `2026-09-08T04:29:05Z`

The image labels contain those exact three source identities. Its installed
distributions contain neither `agent-governor` nor `receipt-kernel`. The four
embedded companion binaries were hashed during qualification; the hashes are
recorded with the NAS results.

## Environment

- host kernel: Linux `6.5.0-44-generic`, x86-64
- Docker client/server: `29.1.3` / `29.1.3`
- image Python: `3.11.16`
- image Codex CLI: `0.146.1`
- ag-ng CLIs: `0.1.0`
- Docket identity: exact source commit and binary hash (its CLI exposes no
  version output)

Companion binary SHA-256 values:

- `ag-loopctl`: `da82f3e236e116cfab56809a19ce811fd9c421e0e98d8072e79b6c37e1d21a07`
- `ag-providerd`: `3c12c549831eb6893f361d0bd1f67150d36e10ec1c2bb79cd22b0cedd6510e5a`
- `ag-providerctl`: `e2696bffc465c1a4726d4fd3bc625975b2afdb5b24593fffb4912f78e8fb52b4`
- `docket`: `21e07e8b3fe143e7e13b0ba0aaf8c6c363539646271193ffa368cd7fbe0c2c2b`

The already-qualified companion commits retain their full-suite and
cross-process VM evidence under `.gate3/qualification-vm/`. The candidate image
was then qualified as a complete application stack with isolated Docker
volumes and a deterministic local HTTP provider.

An initial attempt to put live `ag-providerd` custody on the NAS correctly
failed because root-squashed ownership did not match the configured root-owned
custody. Qualification moved live databases and sockets to isolated Docker
volumes. It did not relax the daemon's custody policy. Config, separately
recoverable keys, and results remain on the NAS.

## Results

- Ruff check and format check: pass.
- Python release suite: **448 passed, 1 skipped**.
- package wheel and source distribution: built successfully.
- all four Compose overlay configurations: valid.
- Playwright reliability suite: **4 passed**.
- image startup/readiness: pass; readiness reported ag-ng authoritative,
  provider socket ready, Docket custody enabled, and classic fallback false.
- real `ag-providerctl` / `ag-providerd` HTTP generation: pass.
- accepted response provenance: actual route `qualification-local`, upstream
  model `qualified-upstream`, normalized usage 11 input / 6 output / 17 total,
  known local API cost zero.
- worker crash after provider dispatch: replacement worker recovered provider
  custody, inserted exactly one authored result, and did not execute a second
  provider request.
- revision acceptance CAS: a provider response completed after an author edit
  but was blocked as `session revision changed`; no response text entered the
  session and no retry occurred.
- dispatch kill switch: stopped new dispatches while inspection and historical
  accepted-result replay remained available; no synchronous fallback occurred.
- previous-image compatibility: `marginalia:opsloop-58decaf` read the copied
  migrated state, wrote a new session message through the legacy compatibility
  link, and this candidate read that exact write afterward.
- evidence recovery: ciphertext restored from retained evidence was decrypted
  in a fresh container using the separately copied keyring. Key version
  `qualification-evidence-v1` and total usage 17 were recovered.

The three application dispatches produced three Docket executor attempts, all
durably `success`. Application disposition was two accepted candidates and one
revision-blocked candidate. The deterministic provider saw exactly three HTTP
requests, including only one for the killed-worker case.

## Evidence custody

Qualification inputs and results are under:

```text
/tank/nfs/marginalia/ag-ng-migration/qualification/8d672105/
```

The separate recovery copy and restored ciphertext sample are under:

```text
/tank/nfs/marginalia/ag-ng-migration/recovery/8d672105/
```

Directories are mode `0700`; secret and config files are mode `0600`. The key
version is recorded without recording key bytes. Live-store expiry cannot erase
ciphertext already retained in a backup; backup retention must therefore be
managed separately.

## Decision

Gate 6 application qualification passes. This is a production-ready candidate,
not a production deployment. Production remains gated by explicit approval and
the preconditions in `PRODUCTION-DEPLOYMENT-PACKET.md`.
