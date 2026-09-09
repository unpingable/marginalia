# Production provider repair and qualification

Status: in progress; production deployment readiness remains invalidated.

## Boundaries

- Validation-only OpenAI, Anthropic, and Moonshot API keys never enter Erin's
  catalog, production secrets, or production containers.
- Every predeployment application-acceptance test uses a synthetic project or
  an isolated production-state clone, never an Erin project.
- Root Ollama on `11434` is unchanged before cutover. GPU experiments use an
  isolated listener. Any root-service override is prepared and recorded but is
  activated only during the authorized cutover, followed immediately by a
  `11434` qualification and rollback on failure.
- Configured/requested provider and model identify frozen work. Observed
  provider/model identity is separate evidence. A command tool's configured
  argument is never reported as the physical identity; absent attestation is
  recorded as `unavailable`.

## Preserved unknown OpenAI attempt

Dispatch
`sha256:777c94a517232f88eaf239ad58a42da8234234db7c89794dc6e9025ca7a06248`
is durably encrypted on the NAS and remains `outcome_unknown` unless new
authoritative evidence appears. See `VALIDATION-ONLY-PROVIDER-QUALIFICATION.md`.

## Orion diagnosis

The production tag remains `orion-marginalia:latest`, digest
`cc1c59d7d41149d62fff9aeb1a02589d005248f24955bd5d59e6ecd433c66e66`,
with a 24,576-token configured context. The host has an RTX 5060 Ti (16,311 MiB)
and NVIDIA driver 580.173.02; root Ollama is snap 0.32.14 revision 131.

Root-service logs show CUDA memory-query/allocation failure. Isolated direct and
snap-profile listeners using CUDA v12/v13 could author through ag-ng but reported
`size_vram=0`; those are CPU controls and do not qualify GPU execution. A
separate one-GPU-layer qualification tag was prepared without modifying the
production tag:

`orion-marginalia-gpu1-qualification:latest`  
`f8a25155b2d1a7b013b7bbf111292fdfc5bbfb81301cda5d173bf25cf85efd08`

Testing it through root `11434` would intentionally load a large model in the
production Ollama process and therefore remains an explicit production-resource
decision, not an isolated-listener qualification.

## Provider catalog

Authenticated, non-generating Moonshot discovery returned only
`kimi-k2.7-code` and `kimi-k2.6`. The configured `kimi-k3` identity is therefore
retained but explicitly unavailable; neither K2 model is substituted.

## Anthropic diagnostic live receipt

A synthetic-project-only Anthropic request passed the complete
ag-ng → Docket → encrypted evidence → revision/canon-checked Marginalia
acceptance path. It made one physical dispatch and used a 32-token output cap:

- configured provider/model: `anthropic-live` / `claude-sonnet-5`;
- observed provider: unavailable in the provider response (the Messages response
  has no physical-provider field);
- provider-attested model: `claude-sonnet-5`;
- usage: 29 input, 13 output, 42 total tokens;
- terminal status: accepted, with the exact requested synthetic marker;
- logical request: `gen_acb1dcaf46df4897af7d742f583bd9fa`;
- dispatch: `dsp_ab394bcdd13144ad94186257ec3e2f8b`;
- candidate: `sha256:b71a3147ff6259e62905f6d3e4d02e0b74ec7ae455f9d32c0cb96117ee7df9e3`.

The validation credential was removed and an exact-byte scan found zero copies
in the retained tree. The complete tree is encrypted at
`/tank/nfs/marginalia/ag-ng-migration/qualification/anthropic-live-20260909`;
archive SHA-256
`f440d2d0202d94854842efc2a1b00b75a266622c8471b4768d2f5cba4c35ef2d`.
All five SQLite databases passed integrity checks, and the archive was restored
and reverified using the separately recoverable NAS key. Plaintext qualification
roots were removed afterward.

That diagnostic image incorrectly copied the configured route into its
`observed_provider_id` field despite the response not attesting one. The review
caught and repaired that provenance regression. Consequently this receipt proves
dispatch/custody/acceptance and the attested model, but it does not qualify the
corrected observed-provider presentation; the exact frozen candidate must repeat
the bounded Anthropic probe.

## Kimi qualification status

Two distinct full-path Kimi attempts ended in definitive HTTP 502 command
failure, each with one physical dispatch. Neither was indeterminate or retried
under the same identity. The fixed launcher reports version 0.41.0, the
non-generating provider listing reports OAuth enrollment with four models and
default `kimi-code/k3-256k`, and `kimi doctor` succeeds. ag-ng deliberately
discards raw command stderr and retains only a bounded `command_refused` result,
so the current evidence cannot honestly distinguish quota, provider refusal, or
another CLI exit.

Both complete failed trees are encrypted and restore-verified on the NAS at
`kimi-live-failure-20260909` and `kimi-live-failure-20260909-2`; archive digests
are `ab9371d2755cbd8864f1a7b9e124223e136507db397579e48ce134cb53458ed1`
and `92256906b757f7687944677f1a7a86f9c2c3c6240c037d20f8cb0314076b6874`.
Their plaintext roots were removed. A third diagnostic generation was not run;
it requires an explicit expansion of the bounded live-attempt authorization.
