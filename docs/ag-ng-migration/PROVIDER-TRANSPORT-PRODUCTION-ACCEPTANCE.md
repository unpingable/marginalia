# ag-ng provider transports — production acceptance

Accepted: 2026-09-08T10:39Z

## Exact release

- Marginalia: `9ea6093046394d0707d8961c2527592937945f79`
- ag-ng: `1bd4225a94fa82df066923612f713a93ba93a7bb`
- Docket: `181589f910b76030b312d6478bd0ac813a630855`
- image: `marginalia:ag-ng-9ea6093-exact`
- image ID: `sha256:7fbd4070cb6a4217525599bc6d4203fff2aecd76d00d01aa60ec42b67d0a9047`
- deployment ID: `crow-household-ag-ng-9ea6093`

The application and companion commits were frozen and pushed before the image
was built. All five production services run that image ID. The image labels bind
the three source commits above; classic Agent Governor and receipt-kernel
distributions are absent.

## Qualification

- Ruff check and format check passed for all 100 source/test files.
- The source suite passed 470 tests; its conditional exact-companion witness was
  the sole skip because paths were not supplied in that run.
- The complete suite, supplied `ag-loopctl` and `docket` extracted from the exact
  image, passed **471 tests with no skips**. This executed the cross-process
  dispatch/custody witness.
- Browser startup passed and Playwright passed 4/4 reliability cases.
- The exact image's daemon and client loaded the deployed production-shaped
  configuration. It contains 8 physical provider policies, preserves all 10 UI
  selections, and exercises credentialed HTTPS, allowlisted local HTTP, and
  structured command transports.
- Production readiness reports ag-ng authoritative, Docket custody enabled,
  provider socket ready, catalog valid, and classic fallback false.

The final cutover found and repaired a paused-state gap: pre-repair synthetics
could leave undispatched queued records and repeatedly revisit them. New work
that loses the admission race is now settled without inventing a dispatch;
pre-existing custody remains inspectable/recoverable. The worker does not spin
on paused queued custody, each scheduled synthetic has a fresh durable identity,
and operator pause is reported as `PAUSED`, not as a provider outage. A live
final-image probe returned `PAUSED` and created no request.

## Provider evidence

The deployment-parity fixtures used synthetic credentials and fake
endpoints/processes. They were not counted as live provider qualification.
Bounded live checks using existing operator credentials/subscriptions recorded:

- OpenRouter GLM Flash: passed; the final-image production smoke reported 285
  prompt and 77 completion tokens. Provider cost remained honestly unavailable.
- Claude command subscription: passed before the application-only paused-state
  correction; its companion/configuration path is unchanged.
- Codex provider-default command route: passed before that correction; its
  companion/configuration path is unchanged.
- Orion local HTTP: retained but unavailable because the enrolled endpoint
  returned terminal HTTP 500 after CUDA out-of-memory while another local model
  workload was active. No workload was disrupted and no substitute was used.
- Kimi command subscription: retained but unavailable because the subscription
  returned its weekly quota HTTP 403. No substitute was used.
- Moonshot API, OpenAI API, and Anthropic API: retained but unavailable because
  their production credential files are absent.

Unsupported or unavailable routes remain in Erin's catalog and identify their
own provider/reason without exposing credentials.

## Production state and writer smoke

The initial layout transition encountered both the legacy shared directory and
a target directory containing only a duplicate maintenance marker. After
verifying the target contents, that duplicate was removed and migration moved
the legacy state intact. The final census is 2 projects, 7 sessions, 747
messages, 2 artifacts, and 133 canon reviews, with no untracked session files.

The final affected-path smoke used a temporary session and OpenRouter selection:

- request `gen_6be5dfb770954f559b337badf8cdf3f6`;
- one logical request, one physical dispatch, one candidate, one accepted
  insertion;
- exact replay returned the same two committed messages without redispatch;
- actual route `openrouter`, actual model `openrouter-glm-5.3-flash`;
- temporary session deleted after acceptance.

Both `default` and Doverton ended with **Generation paused**, and no queued,
dispatching, executing, unknown, candidate, or failed generation remains. The
UI is available with maintenance inactive; enabling generation admits new work
only and never selects a classic or synchronous bypass.

## Backup, keys, and rollback

The definitive post-deployment backup is
`/backups/erin/marginalia-erin-20260908T103819222255Z.zip`, SHA-256
`bfdba72a634633eaec829ddcc6c28b133b17bdc048375479c2298f94c719fcf4`.
Verification and isolated restore loaded the exact census above. The active
evidence key version is `marginalia-evidence-v1`; the separately recoverable NAS
copy and decrypt-tested sample are documented in `docs/gate3/NAS-KEY-RECOVERY.md`.
Live evidence expiry does not erase retained backup ciphertext.

The pre-final selector files, pre-provider configuration, pre-cutover backups,
and protected service logs remain available for rollback/incident evidence.
Superseded qualification containers and volumes were removed; superseded
configuration staging was archived under production cutover evidence. Rollback
must first stop new dispatches and reconcile custody; it cannot establish that a
provider execution or billing did not occur.

Observer: Codex, acting under the owner's explicit provider-repair and production
cutover authorization.
