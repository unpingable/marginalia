# Handoff — missing provider credential wedges generation as "unknown" (2026-09-15)

Audience: Kimi (implementation). Status: **implemented and deployed**
(2026-09-16, branch `incident/generation-policy-20260911`). Production
disposition is recorded separately at the bottom and must be kept current by
the operator.

## Symptom

Erin (Doverton, project `fc3b21be00c3`) saw, indefinitely:

> The provider outcome is still unknown. Marginalia is retaining custody and
> will keep reconciling; starting another generation is not yet safe.

- logical request `gen_895c2e17134a4854becd05f743a6795b`, dispatch
  `dsp_cc152e55489541e881f2e0f506eb55a8`, model `openai-gpt-5.4`
  (route `openai-api`), created `2026-09-15T19:44:41Z`;
- provider dispatch
  `sha256:4dc5eea273b0114f94d202c5c79faa5f695f9793af99d19dde769d743868c33e`;
- `provider_dispatch_started` → `dispatch_outcome_unknown` in 27 ms;
- the worker re-ran `ag-loopctl recover` every ~2–5 s thereafter
  (>2,290 command-log triples for this one dispatch).

The host reboot earlier that day was **not** causal.

## Root cause (verified against the deployed source)

The vendored `ag-ng` tree is identical between deployed commit `d420ba4` and
HEAD. In `ag-ng/crates/ag-providerd/src/lib.rs`:

1. `execute_inference` durably commits the dispatch reservation
   (`provider-dispatch.reserved.v1` + `custody-reserved.v1`, ~L447–483),
   by design *before* any credential load or network I/O.
2. `dispatch()` then reads the endpoint credential from
   `$CREDENTIALS_DIRECTORY` (~L722–734). `openai-api-key` was never
   provisioned (only `openrouter-api-key` and `rpc.pk8` exist), so
   `read_provider_credential` returns `CredentialUnavailable` **before**
   `.send()` (~L744).
3. The API error mapper (~L1566) maps `CredentialUnavailable` to wire code
   `Indeterminate`. The dispatch phase stays `Reserved` forever;
   `fetch_inference` on a `Reserved` phase also returns `Indeterminate` (~L544).
4. Marginalia's gateway (`src/gov_webui/ag_provider_gateway.py`, `_ok_response`)
   raises `ProviderOutcomeUnknown`; the executor marks the dispatch `unknown`;
   `GenerationStore.mark_failed` deliberately refuses to leave `unknown`. No
   product path can settle it.

So a pure **configuration** defect (catalog offers a model whose credential is
absent) is converted into a permanent **custody** ambiguity, even though the
code path proves no request left the host.

The same latent defect applies to `kimi-k3` (`moonshot-api-key`) and
`anthropic-claude-sonnet-5` (`anthropic-api-key`). `/v1/models` reported all
three as `available: true`.

## Required changes (container-sized; keep each independently qualifiable)

### 1. ag-providerd: credential absence must be a definitive pre-dispatch refusal

Upstream is canonical AG (`/data/git/ag_ng`, `constellation-ag`); Marginalia
vendors it (`AG_NG_CONTRACT_COMMIT`). Change upstream, then re-vendor.

Preferred: resolve and validate the credential (and every header) **before**
committing the reservation, and return a distinct non-indeterminate error
(e.g. `ApiErrorCodeV1::Unavailable` / `CredentialUnavailable`) with no
reservation written. If ordering must stay (budget-before-credential), add a
terminal dispatch phase such as `RefusedBeforeSend { ground }` written
atomically on that path, so `fetch_inference` returns a definitive refusal, not
`Indeterminate`.

Must preserve: a timeout or any error after `.send()` begins remains
`Indeterminate`; a crash between reservation and send remains `Reserved` →
`Indeterminate`; budget is never restored or double-charged.

Tests (next to `retry_of_reserved_dispatch_is_indeterminate_and_never_charged_twice`):
- missing credential file → definitive refusal, no network attempt (use a
  transport that fails the test if contacted), no `Reserved` phase left behind;
- empty / multi-line / wrong-permission credential → same;
- credential present + connect-timeout → still `Indeterminate`.

### 2. ag-providerd: startup and readiness must surface missing credentials

- `--check-config` (or a new `--check-credentials`) enumerates every
  `credentialed_https_api` endpoint and reports which `credential_name`s are
  absent/invalid, without printing values.
- Expose content-free per-endpoint readiness over RPC (e.g.
  `endpoint_readiness` → `{endpoint_id: ready|credential_unavailable}`).

### 3. Marginalia: never offer a model providerd cannot authenticate

- At web/worker start (and in `/health/ready` detail), query providerd endpoint
  readiness and project `available: false` with a typed
  `unavailable_reason` for models whose endpoint is not ready. The catalog
  already supports `availability: "unavailable"` +
  `unavailable_reason` (`src/gov_webui/model_providers.py`); runtime readiness
  should compose with it, not replace it.
- `require_available` refusal must happen before `reserve_dispatch`, yielding
  the existing typed `provider_unavailable` outcome (composer shows "Choose
  another available model").
- Defense in depth in the executor: treat a gateway-reported definitive
  pre-send refusal like the existing preparation-failure branch
  (`generation_executor.py`, `mark_failed(..., failure_type="provider_unavailable")`).
- Browser/API regression: a catalog model with a missing credential is visibly
  unavailable and cannot be submitted (per OPERATIONS.md "Generation-control
  semantics are end-to-end").

### 4. Worker: reconciliation must not hot-loop

`generation_worker.run_once` re-drives every `UNKNOWN` request each poll
(`MARGINALIA_GENERATION_POLL_SECONDS=2`). Observed: 134k log lines, ~6.8k
command-log files per stuck dispatch, and a 1.2M-file workspace backup.
Add bounded exponential backoff per request (persisted, e.g. `next_reconcile_at`)
and cap retained command logs for repeated identical `inspect`/`recover`
results. Do not change the rule that restart never implies repeat safety.

### 5. Transport-failure classification (separate, pre-existing)

Two synthetic requests in `erin-writing` have been `unknown` since 09-12/13:
`gen_8eed3d21…` (`transport_failure` class `connect`) and `gen_5324f9c9…`
(class `body`). providerd holds them `Available` but never acknowledged.
Determine whether the gateway should treat `connect`-class failure (provably
pre-send) as a definitive failure, while `body` remains unknown. Qualify before
changing; do not settle these rows ad hoc.

### 6. Operator disposition tooling

Both this incident and 2026-09-11 needed a hand-written SQLite compare-and-set.
Provide a supported `gov_webui.ops` command to apply an operator disposition
to one `unknown` dispatch, requiring: exact request/dispatch ids, expected
`updated_at`, a typed evidence ground (e.g. `providerd_refused_before_send`),
and live verification of the providerd phase. It must write the same
`dispatch_failed` event shape as `mark_failed` and refuse if a candidate exists.

### 7. Backup worker (unverified, low priority)

`marginalia-backup` (read-only `/data`) logged `cannot snapshot SQLite source
…/erin-writing/marginalia/docket-state/state.sqlite: unable to open database
file` at 16:02 startup. Hypothesis only: journal churn from (4) presents a hot
journal to a read-only opener. Confirm or refute after (4).

## Acceptance

- Unit/integration tests above; the exact-binary companion witnesses in the
  `container` CI job still pass and a new witness covers "missing credential →
  definitive refusal, no `Reserved` residue" using exact `ag-providerd` /
  `ag-providerctl` binaries.
- Production-shaped check: with a catalog model whose credential is absent,
  `/v1/models` reports it unavailable and a forced API submission returns
  `provider_unavailable` with no providerd reservation event.
- No commits from this handoff until reviewed by James.

## Production disposition log (operator-maintained)

- 20:57Z maintenance notice enabled (`/data/.marginalia/shared/maintenance.txt`).
- 21:00Z verified backup `marginalia-erin-20260915T210026158586Z.zip`,
  sha256 `9cfc84d07c54f479f3847f607625b92b6e6d2ebb5326891c045b96d67ce5d6c0`.
- 21:0xZ re-verified: `openai-api-key` absent; providerd `fetch_inference` still
  `indeterminate` (phase `reserved`).
- Orion owning-layer gate (supersedes the 09-09 stop condition): Ollama snap
  rev 134 / 0.33.3; host CUDA via Ollama's `cuda_v13` libcudart — set device,
  meminfo 15,709/15,845 MiB free, 1 GiB malloc/free exact, reset OK. Exact tag
  `orion-marginalia:latest` digest `cc1c59d7…` (`num_ctx 24576`) loaded with
  13,111 of 14,357 MiB in VRAM; warm 86 tok/s; `/v1/chat/completions` returned
  content with `finish_reason=stop` at a 2,048-token budget (catalog default
  `max_output_tokens` 4,096). Note: Orion emits reasoning first; small budgets
  yield empty content.
- 21:17Z settlement rehearsed on a disposable in-container copy of the project
  `generation.sqlite`: dry run rolled back; apply wrote dispatch/request
  `failed` + one `dispatch_failed` event; second apply refused on changed
  preconditions. Copy removed.
- 21:22:09Z (operator script) re-verified evidence, then applied the bounded
  transition to `gen_895c…` / `dsp_cc152e55…`: `BEGIN IMMEDIATE`, preconditions
  (exactly one dispatch; both rows `unknown` with `updated_at`
  `2026-09-15T19:44:44.264412+00:00`; exact provider dispatch; route
  `openai-api`; no candidate), each UPDATE required to affect exactly one row,
  event `dispatch_failed` with disposition
  `incident-2026-09-15-credential-unavailable-predispatch`,
  `failure_type=provider_unavailable`. Untouched and retained as residual
  custody: providerd dispatch phase `reserved`; `executor_attempt` state
  `executing`; Docket `governed_loop_attempt` `indeterminate`; ag-loopctl
  campaign `reconciliation_required`. The worker no longer selects the request
  (FAILED with 1/1 dispatches used, empty fallback policy); zero worker results
  for it after the restart.
- Catalog installed at `/tank/nfs/marginalia/ag-ng/production/config/providers.json`
  (sha256 `eb818f37…`; prior `1aea0d49…` kept as `providers.json.pre-20260915`),
  marking `kimi-k3`, `openai-gpt-5.4`, `anthropic-claude-sonnet-5` unavailable.
  `docker restart marginalia marginalia-generation` (providerd, synthetic,
  backup untouched); web healthy; both containers carry the new catalog digest;
  `/v1/models` reports exactly those three `available=false`.
- `GET /v1/generations/gen_895c…` now returns `outcome=failure`,
  `failure_type=provider_unavailable`, `retryable=true`.
- 21:23Z maintenance lifted (renamed `maintenance.resolved-20260915T2123Z.txt`).
- 21:23:35Z one prod-path Orion synthetic
  (`synthetic_worker --once --model orion-local`, isolated
  `erin-writing-synthetic` context): PASS, 28,342 ms, receipt
  `sha256:83b5594e8a4f4a3edffe6b5d1a56bc0d02543d5d1fcefbf4db50f16830c2a258`;
  logical request `gen_4d162a35d01244678fa324a0863669bb` `accepted`; providerd
  `completed` + `governor-custody-acknowledged`; model resident 13,111/14,357 MiB
  VRAM. No new non-terminal work in any context.
- PENDING: Erin's browser acceptance (refresh, open Doverton, choose Orion or a
  GLM model, complete one ordinary turn).

2026-09-16, fix train deployed:

- 15:12Z verified pre-deploy backup `marginalia-erin-20260916T151209391877Z.zip`,
  sha256 `9ef809c772eb3f3e04ad5af3b7dd284258f62b11a5f5bef3b7d584e77a5318fe`;
  maintenance notice enabled for the deploy window.
- Deployed `marginalia:provider-credential-refusal-51c7ec5-candidate` (branch
  `incident/generation-policy-20260911`, commits `3073e47`, `b351dfc`,
  `51c7ec5`; vendored ag-ng `61b56fd8` from upstream
  `marginalia/credential-refusal-v1`) to web, worker, synthetic, backup.
  Prior `.env.ag-ng` and compose kept as `*.pre-provider-credential-refusal-20260916`;
  pre-deploy container logs in `logs-pre-provider-credential-refusal-20260916/`;
  rollback tag `marginalia:rollback-provider-credential-refusal-20260916`.
- providerd alone stayed on `marginalia:generation-policy-d420ba4-candidate`
  (`MARGINALIA_PROVIDERD_IMAGE` pin): the new binary refuses the existing store
  ("store activation identity mismatch" — activation binds exact binary bytes)
  and upstream's rotation ceremony is not implemented yet
  (`ag-ng/docs/deployment.md`). Until providerd rotates, the new pre-dispatch
  credential/command validation and the EndpointReadiness RPC are dark: the
  worker logs `provider_readiness_error` ~every 30 s and the web's readiness
  projection fails open. The three unprovisioned hosted models stay unavailable
  via catalog config, so selection safety is preserved; per-dispatch
  pre-validation is not yet live. **Every future ag-ng binary upgrade is
  blocked on this rotation.**
- Settlements through the new code: Erin's 09-15 22:34Z GLM wedge
  `gen_33d65c5f…` → `failed/provider_execution` (ground
  `providerd_terminal_response`; the provider returned a terminal empty body at
  the 4,096-token output budget on a reasoning model — not a credential
  defect), verified `outcome=failure`, `retryable=true` via the status
  endpoint. Synthetic `gen_8eed3d21…` → `failed/provider_unavailable`.
  Synthetic `gen_5324f9c9…` (body-class; may have reached OpenRouter) stays
  `unknown` under persisted exponential backoff (observed intervals
  4→11→21→41→83 s).
- Command-log backlog pruned to the 100-triple retention (300 files) for all
  three reconciled dispatches; worker CPU ~1.2%. The inert 09-15 `gen_895c…`
  backlog (3,517 triples, 19:44–21:22Z) is retained: the worker no longer
  selects that request, and with dedupe suppressing writes no prune pass runs
  for it.
- Adapter probes (isolated `erin-writing-synthetic` context): Orion PASS
  23,183 ms receipt `sha256:8c7b6a4e…`; `claude-sonnet-4-20250514` (Claude
  Code) PASS 4,577 ms receipt `sha256:dcd70868…`; `kimi-code-local` PASS
  11,586 ms receipt `sha256:53481bf9…`. `codex-default` fails cleanly —
  terminal provider 502 in 5.6 s, generation settled, no `unknown` residue
  (last PASS 09-08; unexercised since the pause days; adapter/upstream issue,
  not a custody wedge).
- Item 7 confirmed and fixed. `_sqlite_snapshot` could not open a WAL database
  from the backup container's read-only `/data` mount: WAL recovery must
  create the `-shm` file beside the source, so even a read-only open fails
  with "unable to open database file". Deterministic and unrelated to the hot
  loop — it reproduced 16 minutes after the loop ended, and zero automated
  backup attempts had succeeded since the ag-ng migration (verified backups to
  date were operator-made from a writable mount). Fixed in `d52fd5b` (stage
  db+wal+shm through `_read_stable` into a writable temporary directory; WAL
  checksum chain plus destination integrity check gate the result) and
  deployed as `marginalia:provider-credential-refusal-d52fd5b-candidate` to
  all Python services (providerd pin unchanged; `.env.ag-ng` kept as
  `.env.ag-ng.pre-backup-fix-20260916`). Live proof: the docket-state source
  snapshots in 0.03 s; fresh backup
  `marginalia-erin-20260916T155743313238Z.zip` verified (2 projects, 7
  conversations, 19,211 files, outer checksum ok, docket-state member
  present), sha256
  `d218084dd98adf85c4f42b6b3e75221c46925a5aad29bc2cdf28b05ad3f3eca7`. Watch
  tomorrow's automated daily attempt in the backup log.
- 15:58Z maintenance lifted (`maintenance.resolved-20260916T1558Z.txt`) after
  an Orion re-probe on the final image (PASS 29,647 ms, receipt
  `sha256:e8a66584…`).
- Production-shaped acceptance: a forced `/v1/chat/completions` to `kimi-k3`
  (credential absent) returns `failure/provider_unavailable` ("The selected
  model provider is unavailable. Choose another available model or try again
  later.") with no session mutation and no generation row — refusal happens
  before custody.
- PENDING: Erin's browser acceptance (refresh, open Doverton, choose Orion or
  a GLM model, complete one ordinary turn).

### Loose end: config file modes

The NFS production config files (`providers.json`, `providerd.toml`,
`providerctl.toml`) are `root:root 0600` from the 09-08 repair, which made a
routine catalog edit require sudo. `providers.json` and `providerd.toml` hold
no secret values (credential names only). Entrypoints already re-install
them as private copies inside containers (`install_private_file`), so the
NFS source modes can likely be relaxed; decide deliberately, don't widen
`secrets/`.
