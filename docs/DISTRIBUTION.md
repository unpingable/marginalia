# Marginalia ag-ng appliance distribution

The supported distribution is one immutable OCI image used by a versioned
multi-service Docker Compose deployment. The classic single-container launcher
and installer fail closed.

## Image contents

- Marginalia `0.1.0`;
- ag-ng `ca1713a277b587929a6138ff6dbd3f07b8fe676d`;
- Docket `181589f910b76030b312d6478bd0ac813a630855`;
- vendored read-only `receipt-v1` `0.1.0`;
- Codex CLI `0.146.1`;
- `ag-providerd`, `ag-providerctl`, `ag-loopctl`, and `docket`.

The image must not contain the `agent-governor` or `receipt-kernel` Python
distributions. The Dockerfile and CI assert this.

## Release order

1. Freeze and commit the candidate.
2. Build from that exact commit and record its OCI digest and labels.
3. Validate that digest in an isolated deployment, including provider RPC,
   Docket recovery, browser reliability cases, backup/key restore, and
   previous-image read/write compatibility.
4. Repair only by creating a new committed candidate and rerunning affected
   validation.
5. With separate production approval, deploy the already-qualified digest.
6. Record acceptance in a later documentation-only commit.

No production image is built from uncommitted work.

## Deployment inputs

The operator provides an ignored `.env` plus two NAS-backed paths:

```text
config/providers.json
config/providerd.toml
config/providerctl.toml
secrets/marginalia-ag-issuer.pk8
secrets/marginalia-evidence-keys.json
secrets/provider-identities.json
secrets/providerctl/rpc.pk8
secrets/providerd/rpc.pk8
secrets/providerd/<provider-credential>
```

All files are `0600`; directories are `0700`. Provider credentials never enter
`.env`, Git, the web container, project exports, or data-volume backups. See
[MODEL_PROVIDERS.md](MODEL_PROVIDERS.md) and
[EVIDENCE-SECURITY.md](gate3/EVIDENCE-SECURITY.md).

## Acceptance boundary

A clean qualification deployment must prove:

- every service uses the candidate digest;
- both pinned source identities are embedded and correct;
- config parsers accept root-owned in-container copies and reject unsafe input;
- providerd/providerctl completes a real cross-process request;
- Docket preserves one attempt across worker restart and lost acknowledgement;
- reload, double-submit, two-tab acceptance, canon/guidance races, kill-switch,
  context maintenance, synthetic probe, and evidence expiry behave as specified;
- the previous image can read and write the upgraded state, and the candidate
  can read the result;
- a separately restored key decrypts a restored evidence sample;
- backup retention is documented as retaining old ciphertext even after live
  evidence-body expiry.

Production deployment is a separate decision. For the current migration it is
also blocked until the previously exposed OpenRouter credential is revoked and
replaced by a file mounted only into ag-providerd.
