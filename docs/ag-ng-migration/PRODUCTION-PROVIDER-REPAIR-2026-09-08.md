# Production provider repair and qualification

Status: in progress; production deployment readiness remains invalidated.

## Boundaries

- Validation-only OpenAI, Anthropic, and Moonshot API keys never enter Erin's
  catalog, production secrets, or production containers.
- Every predeployment application-acceptance test uses a synthetic project or
  an isolated production-state clone, never an Erin project.
- Root Ollama on `11434` is unchanged before cutover. GPU experiments use an
  isolated listener. The proposed CUDA-v12 root-service override was disproved,
  moved into a `DISQUALIFIED -- DO NOT ACTIVATE` NAS evidence directory, and
  must not be installed. Any later root-service change requires a newly
  qualified replacement and its own rollback.
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

Root-service logs show that the canonical snap service selects `cuda_v13` and
can discover the RTX 5060 Ti, but its historical model loads observed only about
2.5 GiB free and then failed CUDA memory queries/allocations. Forced `cuda_v12`
and `cuda_v13` transient listeners reported `size_vram=0`; those are CPU controls
and do not qualify GPU execution. CUDA-v12 is therefore specifically
disqualified on this RTX 5060 Ti boundary.

An exact `snap run ollama.listener` was also started inside a disposable network
namespace so it could bind its own `11434`. It had the same
`snap.ollama.listener` AppArmor label as production, but snap-confine placed it
in a transient scope rather than the canonical service cgroup and it discovered
CPU only. The different cgroup/scope is correlated with that result, but device
denial by the transient scope is only a hypothesis: both processes ran as root,
used seccomp mode 2, and had the same enforcing AppArmor label. The causal
mechanism was not established. The disposable unit and namespace were removed.

A separate one-GPU-layer qualification tag was prepared without modifying the
production tag:

`orion-marginalia-gpu1-qualification:latest`  
`f8a25155b2d1a7b013b7bbf111292fdfc5bbfb81301cda5d173bf25cf85efd08`

Testing either tag through canonical root `11434` would intentionally load a
large model in the production Ollama process and therefore remains an explicit
production-resource decision, not an isolated-listener qualification.

Three separately identified full-path observations were retained; no unknown
attempt was retried:

- a Docker-to-loopback connection mistake created one `outcome_unknown`
  dispatch before Ollama received a request; encrypted archive SHA-256
  `648a516db4586a1beef49c52b551da8818dadf79b14d59be91815a1cb41af743`;
- forced CUDA-v12 produced a CPU-only candidate, then application acceptance
  definitively blocked it because the 32-token cap contained no authored output;
  encrypted archive SHA-256
  `84b3346317a32e811d1e9975d116b628fd708fcac853ae390383e930be2ed345`;
- forced CUDA-v13 with a 256-token cap completed one full synthetic-project path
  in 22.8 seconds, returned the exact marker, and reported 28 prompt plus 86
  completion tokens, but post-run `size_vram=0` and 0 MiB GPU use classify it as
  an accepted CPU control, not GPU qualification; encrypted archive SHA-256
  `e563254d211b61b57ea45b5d1e5de3e223af0baf38e90be816514fd5f9952718`.

All three encrypted archives passed five-database integrity and separately keyed
restore checks. Their plaintext trees were removed.

### Canonical `11434` qualification

After separate authorization, exactly one synthetic request used the existing
canonical listener with no retry, fallback, service restart, configuration
change, model pull, context reduction, or substitution. Preflight established:

- canonical service active at Ollama 0.32.14 snap revision 131, with `home`,
  `network`, `network-bind`, and `opengl` interfaces connected;
- exact tag digest
  `cc1c59d7d41149d62fff9aeb1a02589d005248f24955bd5d59e6ecd433c66e66`
  and baked `num_ctx 24576`;
- zero loaded models on canonical `11434` and the separately known Gutenberg
  `11436` listener, no NVIDIA compute process, 15,845 MiB host-reported VRAM
  free, and about 60 GiB RAM available;
- historical and live runner selection at
  `/snap/ollama/131/lib/ollama/cuda_v13`.

The request ended in definitive HTTP 500 custody:

- logical request `gen_6ff041365ca44e62bf13d41ff4933a95` (`failed`);
- dispatch `dsp_890e7b68d5b24bf88628bd72644d8f6a` (`failed`);
- provider dispatch
  `sha256:26a83d725aac8bdd7d677c52975c15768d1cdc6319c99758e2f900f94382858e`;
- exactly one physical dispatch and no candidate or application insertion.

Concurrent journal evidence shows the cuda-v13 GPU-discovery watchdog timing
out, then Ollama reusing old memory values of 2.1 GiB available / 2.5 GiB free
while NVIDIA reported 15,845 MiB free. The exact runner launched with
`-c 24576`, `cudaMemGetInfo` returned 0/0, and a 1,137.77 MiB allocation failed.
Retrying with the multimodal projector on CPU ended in the same CUDA error.
Twenty concurrent samples observed only 0--17 MiB nominal GPU use, no compute
application, no loaded canonical model, and no activity on the known `11436`
consumer. No GPU layer placement occurred.

The encrypted custody tree, raw concurrent journal, allowlisted telemetry, and
profile comparison are retained at
`orion-canonical-11434-failed-19962cd-20260909`, archive SHA-256
`5b8119912efe46ffa73ba674584b38f50e548c126ca3e338fb318568a7dc567f`.
All five databases and every restored file passed the separately keyed restore
check; plaintext was removed. Canonical Ollama remained active, responsive, and
idle afterward, so it was not restarted. Production Marginalia remained on its
prior image with both projects paused.

## Provider catalog

Authenticated, non-generating Moonshot discovery returned only
`kimi-k2.7-code` and `kimi-k2.6`. The configured `kimi-k3` identity is therefore
retained but explicitly unavailable; neither K2 model is substituted.

## Anthropic terminal live receipt

A synthetic-project-only Anthropic request passed the complete
ag-ng → Docket → encrypted evidence → revision/canon-checked Marginalia
acceptance path. It made one physical dispatch and used a 32-token output cap:

- configured provider/model: `anthropic-live` / `claude-sonnet-5`;
- observed provider: unavailable in the provider response (the Messages response
  has no physical-provider field);
- provider-attested model: `claude-sonnet-5`;
- usage: 29 input, 13 output, 42 total tokens;
- terminal status: accepted, with the exact requested synthetic marker;
- logical request: `gen_27bb56458dad4fd7bd2faffecc63a824`;
- dispatch: `dsp_f6680b14c1a346199a0ea2cf04950ff2`;
- candidate: `sha256:e2f7f3fa6e4f9b63f7f515a7c8330d236af2c47c47244c5aaf965f34b3a5d6bf`.

The validation credential was removed and an exact-byte scan found zero copies
in the retained tree. The complete tree is encrypted at
`/tank/nfs/marginalia/ag-ng-migration/qualification/anthropic-live-exact-015aaea-20260909`;
archive SHA-256
`8765f0b71ca83e348bc6dd9abe811cd54210dac4829a047cbfd80ba434949f7b`.
All five SQLite databases passed integrity checks, and the archive was restored
and reverified using the separately recoverable NAS key. Plaintext qualification
roots were removed afterward.

This is the repeat after the review caught an earlier diagnostic image copying
the configured route into `observed_provider_id`. The corrected receipt records
the observed provider as unavailable while retaining the provider-attested
model. Commit `19962cd` changes only the Playwright witness after this receipt;
the provider/runtime code and built application layers are unchanged, and the
affected five-case browser suite was rerun successfully.

## Kimi terminal live receipt

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
Their plaintext roots were removed.

After explicit authorization, one additional request passed the complete
ag-ng -> Docket -> encrypted evidence -> revision/canon-checked synthetic-project
acceptance path:

- configured provider/model: `kimi-live` / `kimi-code/k3-256k`;
- observed provider/model: unavailable because the command did not attest them;
- exact marker accepted in 74.9 seconds;
- usage reported by the command: zero input/output/total tokens (retained as
  reported, not treated as proof of zero provider usage);
- logical request: `gen_377bfc02052e4d0dba2beac5a688dee3`;
- dispatch: `dsp_11bfa4f5a1684a798085341c01d02412`;
- candidate:
  `sha256:02cdcac3608962267ca5a21c9f047bc170c9b5175e352e478ce13bc36bbb1761`.

The diagnostic wrapper exited zero with empty stderr and deleted the raw file.
The encrypted NAS archive at `kimi-live-accepted-19962cd-20260909` has SHA-256
`ed1a7bc7d6ce2474048215e4dfc2253fb19d481446c0ac286bb771b3fdbfc492`,
passed five-database integrity and separately keyed restore checks, and its
plaintext tree was removed. An in-memory comparison of 453 credential-bearing
fragments found zero copies in the plaintext receipt or encrypted custody tree.

## Remaining production gate

Anthropic and Kimi now have terminal accepted full-path receipts, and the
production-shaped catalog and exact companion suite pass. Orion remains the
only provider-catalog blocker. Canonical `11434` now has a definitive
cuda-v13/Ollama owning-layer failure: its memory discovery disagrees with NVIDIA
and model startup OOMs before GPU placement. Production stays paused and
unchanged until that owning layer is repaired and a newly authorized exact
request proves nonzero VRAM, or the operator chooses a different explicitly
qualified Ollama deployment boundary. The failed attempt must not be retried.
