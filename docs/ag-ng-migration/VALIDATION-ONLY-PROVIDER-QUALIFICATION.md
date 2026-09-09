# Validation-only provider qualification — 2026-09-08

This is follow-up evidence for the exact production release recorded in
`PROVIDER-TRANSPORT-PRODUCTION-ACCEPTANCE.md`. It does **not** enroll these API
credentials for Erin, alter the production catalog, or supersede the production
acceptance record.

## Isolation and custody

- The OpenAI, Anthropic, and Moonshot credentials supplied under
  `/tank/nfs/secrets` were mounted read-only into a disposable provider daemon
  only. They were never copied into the production provider-secret directory or
  mounted into a production service.
- The disposable daemon used the exact deployed Marginalia image and fresh RPC
  identities, runtime, provider-work, and provider-state directories.
- The daemon was removed after the bounded checks, unmounting all three
  validation-only credentials. Production remained paused and unchanged.
- The complete stopped qualification tree formerly under `/tmp` is now held in
  the established encrypted NAS evidence boundary at
  `/tank/nfs/marginalia/ag-ng-migration/qualification/production-provider-repair-20260908`.
  Its encrypted archive SHA-256 is
  `559e7bae8653114dc1841e411187e6e5d9ff61214d09e3a3dd29d6bb950704b6`.
  A restore with the separately held recovery key reproduced all 16 file hashes
  and passed SQLite integrity checking. The plaintext temporary tree was then
  removed.

## Orion

The production-enrolled `orion-marginalia:latest` route reached its allowlisted
local Ollama endpoint through the full `providerctl -> providerd` path, but the
endpoint returned terminal HTTP 500. The captured body reports CUDA startup
failure at `cudaMemGetInfo` due to out-of-memory. At observation time the RTX
5060 Ti reported 16,311 MiB total, 15,845 MiB free, no GPU process, and Ollama
reported no loaded model. A smaller GPU-layer direct probe failed the same way;
this was not merely contention with another visible workload.

A separate, non-production tag,
`orion-marginalia-cpu-qualification:latest`, was created with `num_gpu 0` and
`num_ctx 4096`. It passed a direct CPU-only probe and then passed a complete
disposable ag-ng dispatch with:

- selection: `orion-local`;
- physical model: `orion-marginalia-cpu-qualification:latest`;
- expected `ORION_OK` response and a present receipt;
- reported usage: 31 prompt, 130 completion, 161 total tokens;
- dispatch identity:
  `sha256:74e90666207ff81a74451c8e15e90a6ad5eb56191969fb773d836ea0c6683794`.

This qualifies Orion's local HTTP transport and the model on CPU at a tested
4,096-token serving context. It does not qualify the existing GPU-backed tag or
the catalog's larger inferred window. Production promotion therefore requires a
separate, explicit operator choice about CPU performance and a truthful context
ceiling; no model tag or production setting was changed during this campaign.

## Validation API credentials

Non-generating authenticated model-catalog requests were made from a disposable
container without printing credential material:

| Provider | Result | Configured model evidence |
| --- | --- | --- |
| OpenAI | HTTP 200; 127 models returned | `gpt-5.4` present |
| Anthropic | HTTP 200; 11 models returned | `claude-sonnet-5` present |
| Moonshot | HTTP 200; 2 models returned | `kimi-k3` absent; account advertised `kimi-k2.7-code` and `kimi-k2.6` |

The Moonshot result establishes authentication/connectivity only. It neither
establishes balance nor qualifies a generation, and the configured-model
mismatch remains unresolved.

One minimal OpenAI generation was admitted through the disposable ag-ng path.
It entered indeterminate custody and did not settle during bounded
reconciliation. It was not retried and must not be treated as safe to retry:

`sha256:777c94a517232f88eaf239ad58a42da8234234db7c89794dc6e9025ca7a06248`

The record contains reservation/custody evidence but no completion. A read-only
`fetch_inference` against the preserved daemon state, with no API credential
mounted, returned `indeterminate`. The frozen Chat Completions request did not
request provider-side storage and contains no response/job identifier that the
provider contract can retrieve. It remains `outcome_unknown` unless new
authoritative evidence appears; it was not and must not be redispatched.

The later repair campaign completed capped Anthropic and Kimi generations
through the full custody and application-acceptance path; their terminal
receipts are recorded in `PRODUCTION-PROVIDER-REPAIR-2026-09-08.md`. No
substitute model or validation API credential was used for either route.

## Remaining decisions

1. Repair the canonical Ollama/cuda-v13 memory-discovery failure at root
   `11434`, or select a different explicitly qualified Ollama deployment
   boundary. The one authorized canonical request failed terminally before GPU
   placement even though NVIDIA reported the GPU free. Parallel snap invocations
   discovered CPU only; transient-scope device denial remains a hypothesis, not
   an established cause.
2. Reconcile the preserved OpenAI dispatch if provider evidence later makes that
   possible; never redispatch it under the same logical attempt.
3. Diagnose why the authenticated Moonshot catalog does not advertise the
   configured `kimi-k3` model before claiming that API route ready.
4. Keep all validation-only API keys prohibited from production; the terminal
   Anthropic and command-backed Kimi receipts do not enroll those API routes.
