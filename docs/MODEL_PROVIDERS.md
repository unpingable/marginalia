# Configured model providers

Marginalia can expose a bounded set of human-selected models while retaining
Agent Governor as the execution and receipt boundary. Provider configuration is
optional. Without it, the existing daemon-advertised backend behavior remains
unchanged.

Set `MARGINALIA_MODEL_CONFIG` to a read-only JSON file mounted by the
deployment. The file is deployment configuration: endpoints, credential
variable names, enabled models, labels, and the default do not belong in the
source repository.

## Schema

```json
{
  "version": 1,
  "default_model": "existing-default",
  "providers": [
    {
      "id": "existing",
      "protocol": "existing-command",
      "models": [
        {
          "id": "existing-default",
          "label": "Existing backend"
        }
      ]
    },
    {
      "id": "local-subscription-command",
      "protocol": "local-command",
      "command": {
        "adapter": "kimi-code",
        "executable_env": "LOCAL_COMMAND_PATH",
        "working_directory_env": "LOCAL_COMMAND_WORKDIR"
      },
      "timeout_seconds": 180,
      "models": [
        {
          "id": "local-command-writing",
          "model": "provider/model-alias",
          "label": "Local subscription model"
        }
      ]
    },
    {
      "id": "claude-subscription-command",
      "protocol": "local-command",
      "command": {
        "adapter": "claude-code",
        "executable_env": "CLAUDE_COMMAND_PATH",
        "working_directory_env": "CLAUDE_COMMAND_WORKDIR"
      },
      "timeout_seconds": 180,
      "models": [
        {
          "id": "claude-writing",
          "model": "sonnet",
          "label": "Claude Sonnet"
        },
        {
          "id": "claude-context-summary",
          "model": "sonnet",
          "label": "Claude Sonnet context maintenance",
          "purpose": "context-maintenance"
        }
      ]
    },
    {
      "id": "local-compatible",
      "protocol": "openai-compatible",
      "inference": "local",
      "base_url": "http://provider.internal:11434/v1",
      "timeout_seconds": 120,
      "models": [
        {
          "id": "local-writing",
          "model": "upstream-model-name",
          "label": "Local writing model"
        }
      ]
    },
    {
      "id": "remote-compatible",
      "protocol": "openai-compatible",
      "base_url": "https://provider.example/v1",
      "api_key_env": "REMOTE_PROVIDER_API_KEY",
      "timeout_seconds": 120,
      "models": [
        {
          "id": "remote-writing",
          "model": "remote-model-name",
          "label": "Remote writing model"
        }
      ]
    },
    {
      "id": "openrouter",
      "protocol": "openai-compatible",
      "base_url": "https://openrouter.ai/api/v1",
      "api_key_env": "OPENROUTER_API_KEY",
      "timeout_seconds": 180,
      "models": [
        {
          "id": "openrouter-glm-5.3-flash",
          "model": "z-ai/glm-5.3-flash",
          "label": "GLM 5.3 Flash (OpenRouter)"
        }
      ]
    },
    {
      "id": "anthropic-api",
      "protocol": "anthropic-messages",
      "base_url": "https://api.anthropic.com/v1",
      "api_key_env": "ANTHROPIC_API_KEY",
      "timeout_seconds": 120,
      "models": [
        {
          "id": "anthropic-claude-sonnet-5",
          "model": "claude-sonnet-5",
          "label": "Anthropic Claude Sonnet 5 (API)"
        }
      ]
    }
  ]
}
```

Provider IDs and configured model IDs must be unique. Configured model IDs are
the values selected by the UI and passed through the governed daemon contract.
The nested `model` value is the exact upstream model ID. A configured ID is
restricted to letters, digits, `.`, `_`, `:`, and `-`, while an upstream model
name may additionally contain `/`, so a namespaced slug such as
`z-ai/glm-5.3-flash` belongs in `model` with a local ID like
`openrouter-glm-5.3-flash` beside it.
An `existing-command` model without a nested `model` delegates to the
existing command's default model behavior.

Model `purpose` defaults to `writing`. A `context-maintenance` model is internal:
it is omitted from writer model lists, rejected by story-generation APIs, and
may be resolved only by the derived-context maintenance path. The configured
default must be a writer-selectable model.

Only `http` and `https` base URLs without embedded credentials, query
strings, or fragments are accepted. `api_key_env` contains an environment
variable name, never a credential. Unknown fields, protocols, models, duplicate
IDs, and malformed URLs fail visibly. Models with missing required credentials
or unavailable local commands remain visible but disabled. If the configured
default is unavailable, Marginalia uses the first available configured model as
the effective default; an explicit unavailable selection still fails without
substitution.

`local-command` is a bounded process transport. Its typed `command.adapter`
selects a supported argument/output contract (`kimi-code` or `claude-code`);
it is not an arbitrary command template. `executable_env` and
`working_directory_env` name environment variables whose values remain
deployment-local.

Bind-mount the host executable through a stable path, not a version-pinned one.
A self-updating CLI replaces its versioned file, and a container that mounts the
old version keeps working on the original inode until it is next replaced — at
which point every model on that provider silently becomes unavailable, including
the internal maintenance model. The failure is reported honestly by `/v1/models`
and `availability_error` as `configured command executable is unavailable`, but
nothing fails until the next container replacement, so it is easy to attribute to
whatever change happened to accompany that restart. Mount the launcher symlink
the installer maintains and confirm `/v1/models` after any replacement.

The Kimi Code adapter invokes
one explicit model in noninteractive `stream-json` mode and uses the final
assistant message. The Claude Code adapter invokes `claude --print` with JSON
output, passes the governed prompt over standard input, and returns the result
plus reported token usage. It disables tools, slash commands, project/user
customizations, and session persistence so a text completion cannot turn into
an ambient coding-agent workflow. Both adapters apply the configured timeout,
terminate their process group on timeout/cancellation, and normalize command
failures without exposing raw stderr. Local commands currently emit whole
assistant messages rather than token deltas, so Marginalia receives a bounded
completion chunk after each command turn finishes.

For an internal Claude `context-maintenance` model, the adapter also supplies
the exact `SummarySections` JSON Schema through Claude's native
`--json-schema` interface. Ordinary Claude writing models do not receive that
schema. Marginalia still parses and validates the returned object, evidence
IDs, and compaction bounds before checkpointing it; native constrained output
does not replace application validation.

For HTTP providers, `timeout_seconds` is a total execution deadline, not merely
a socket timeout. Optional `connect_timeout_seconds` and
`read_timeout_seconds` separately bound connection establishment and response
idle time; each must be no greater than `timeout_seconds`. Continuous SSE data
therefore cannot extend one invocation indefinitely. See
[RELIABILITY.md](RELIABILITY.md) for the outer provider/RPC envelopes.

Size `timeout_seconds` against the slowest realistic turn for that specific
model, not a single house default, and keep it below
`MARGINALIA_GOVERNOR_INVOCATION_TIMEOUT_SECONDS` so the daemon does not expire
first. Local runtimes are the usual surprise: a large local model that is not
resident must be loaded before it emits anything, so the first turn after an
idle period pays a cold-start cost the steady-state timing never shows. A
timeout tuned on a warm model reports that as `provider response became idle`,
which reads like a hang rather than a budget that was always too small. Either
raise the budget or keep the model resident; measure both states before
choosing.

### OpenAI-compatible gateways

OpenRouter is reached through the `openai-compatible` protocol rather than a
protocol of its own: it exposes Chat Completions at
`https://openrouter.ai/api/v1`, takes a bearer credential, and echoes the
requested model. Point `base_url` at the gateway, name the credential variable
in `api_key_env`, and put the routed slug in `model`. Selecting a different
OpenRouter model — `z-ai/glm-5.3` in place of `z-ai/glm-5.3-flash` — is an edit
to that one field; no code knows the slug. A self-hosted or proxied gateway is
the same change with a different `base_url`.

Two behaviours differ from a first-party API and are handled at the transport.
A gateway may return HTTP 200 carrying an `error` object and no `choices` when
an upstream refuses — rate limiting, no permitted provider, moderation. That is
reported as a typed `provider_error` with the upstream status where one is
given, never as a malformed body and never as an empty successful generation.
Gateways also emit SSE comment lines as keep-alives while they route; those are
ignored rather than parsed as data.

Marginalia still requires the response model to equal the requested one, so a
gateway configured to silently reroute to a substitute fails closed as
`model_mismatch`. Configure one explicit slug per entry rather than a routing
alias that may answer as something else.

Reasoning models routed through a gateway spend their completion budget on
reasoning before any answer text, and report `content: null` with a `length`
stop when that budget runs out first. Marginalia does not send `max_tokens` on
generation, so the provider's own default applies and ordinary turns are
unaffected; when it does happen the transport reports a typed
`truncated_response` naming the output limit rather than calling the body
malformed. Reasoning tokens are billed as completion tokens, so usage counts for
these models exceed the visible answer length. Gateways may also return usage
fields beyond the three Marginalia records — cost breakdowns, cache and
reasoning detail — which are ignored rather than rejected.

Attribution headers some gateways accept for leaderboard ranking are not sent.
They are optional for functionality and would require a deployment-specific URL
and title that do not belong in provider configuration.

## The timeout ladder

Five bounds sit inside one another. Each must be strictly larger than the one
it contains, or the outer layer kills the request before the inner layer can
report a clean, classified failure:

```
provider connect_timeout_seconds   (per provider; short on purpose)
provider read_timeout_seconds      <= timeout_seconds
provider timeout_seconds           total for one provider call
MARGINALIA_CODEX_TIMEOUT_SECONDS   provider-command deadline   (0.1-1800)
MARGINALIA_GOVERNOR_INVOCATION_TIMEOUT_SECONDS  supervisor      (0.1-3600)
MARGINALIA_GOVERNOR_CHAT_TIMEOUT_SECONDS        daemon RPC
MARGINALIA_SYNTHETIC_TIMEOUT_SECONDS            liveness worker
```

Two ways this bites, both observed in production:

**A read bound shorter than the real first-token latency.** `read_timeout_seconds`
bounds one wait for provider bytes, and on the non-streaming path nothing
arrives until the whole answer does. Cold local weights need ~30s to load before
emitting anything, and a reasoning model can spend its first 30s producing
tokens a non-streaming caller never sees — one measured GLM 5.3 Flash turn spent
2,073 reasoning tokens and 30.6s before its first visible character. A 30-second
read bound cancels both at the moment they were about to answer, and reports
`read_timeout` / "provider response became idle", which reads like a network
fault rather than a bound that was simply too small. The default is now
`timeout_seconds`; set it explicitly only when you want stall detection.

**An inner bound equal to an outer one.** If a provider's `timeout_seconds`
equals `MARGINALIA_CODEX_TIMEOUT_SECONDS`, the two expire together and the
supervisor's tree-kill wins the race. The provider never gets to raise its own
timeout, so the incident is classified as a provider-execution failure instead
of a timeout, and the operator reads the wrong story. Leave a real margin —
the deployment currently runs 600 / 660 / 680 / 700 / 720.

Size these from measured latency at your *largest* real context, not from a
small smoke test. First-token latency scales with prompt size, and the prompt
grows as the manuscript does.

## Model taxonomy in the picker

The writer's menu is grouped, and the groups are served by the catalog rather
than guessed by the browser. Three properties describe every entry:

| property    | values                          | source                       |
| ----------- | ------------------------------- | ---------------------------- |
| `kind`      | `model`, `agent`                | derived from `protocol`      |
| `inference` | `local`, `hosted`               | **declared per provider**    |
| `access`    | `api`, `subscription`, `open`   | derived from credentials     |

`kind` follows from the protocol: `existing-command` and `local-command` drive
an agent process, everything else calls a model endpoint. `access` follows from
that — the parser forbids `api_key_env` on both agent protocols, so an agent's
credential is always the tool's own login, which is a subscription.

`inference` is the one property Marginalia cannot observe. A base URL's hostname
is a guess, and an absent credential says nothing about where weights run. So
the deployment declares it on the provider:

```json
{
  "id": "ollama-local",
  "protocol": "openai-compatible",
  "inference": "local",
  "base_url": "http://host.docker.internal:11434/v1",
  "models": [{"id": "orion-local", "model": "orion:latest", "label": "Orion 26B"}]
}
```

The default is `hosted`, so a provider that forgets to declare is never
presented to the writer as private. Declare `local` only when the weights run on
hardware the deployment controls. **An agent binary executing locally is not
local inference** — Claude Code, Codex, and Kimi Code all send prompts to a
vendor. Calling those "local" spends the word the writer needs for actual
privacy, so they group as *Subscription agents*.

The four resulting groups are `Hosted models`, `Local models`,
`Subscription agents`, and `Local agents` (an agent adapter pointed at local
inference). Groups appear in the order their first model appears in this file,
so provider order controls menu order.

Because the group carries locality and billing, labels should carry neither:
prefer `Orion 26B` over `Orion 26B (local)` and `Claude Code (Sonnet)` over
`Claude Sonnet`. A label that names the product and its model stays true when
the group changes; one that encodes transport does not.

## Behavior

The model picker lists only models in this file. Selection is stored on the
conversation and affects future generation only. Each newly persisted assistant
message records the configured provider ID and exact upstream model ID.
Historical messages that predate these fields remain readable and display their
identity as unrecorded.

Marginalia does not discover provider models, route requests automatically,
silently fail over, or substitute another configured model after a failure.

The existing native Codex command is supervised by Marginalia rather than
replacing the dispatcher process. `MARGINALIA_CODEX_TIMEOUT_SECONDS` controls
its response deadline, defaults to 240 seconds, and accepts values from 0.1 to
1800 seconds. On timeout Marginalia terminates the Codex process group and
returns a visible provider failure so one stalled command cannot block later
governed requests indefinitely.

The `openai-compatible` transport uses `/chat/completions` and supports system,
user, and assistant messages, non-streaming completions, streaming SSE, bounded
timeouts, and cancellation. This is also the native transport for OpenAI API
models when `base_url` is `https://api.openai.com/v1` and `api_key_env` names
`OPENAI_API_KEY`.

The `anthropic-messages` transport uses Anthropic's native `/messages` request
and streaming event contracts. It sends `x-api-key` and
`anthropic-version: 2023-06-01`, moves system messages to Anthropic's top-level
`system` field, requires an explicit credential environment variable, and
normalizes Anthropic token usage into Marginalia's provider result. This
protocol is intentionally separate from the OpenAI-compatible transport.
The currently qualified Agent Governor Codex-command boundary flattens governed
conversation messages before invoking its configured command, so live
command-dispatched providers retain the same prompt-shaping semantics as the
existing Codex backend.

## Deployment wiring

The deployment must mount the JSON file read-only, set
`MARGINALIA_MODEL_CONFIG` to its container path, and pass only the credential
environment variables referenced by configured providers. A configured API
provider with an unset or empty credential variable remains visible but
disabled and cannot become the effective default. Linux deployments that
need a container-to-host endpoint may add a host-gateway mapping in their
private Compose override. Local-command deployments must also mount the chosen
executable and its provider-owned authentication state outside source control.

Do not place deployment endpoints, credential values, enabled household models,
or local defaults in source-controlled Compose files.

## Token budgeting and maintenance model

Configured model entries may declare `tokenizer_encoding` (default
`o200k_base`) and `token_safety_multiplier` (default `1.0`, bounded from
`1.0` to `2.0`). These values identify Marginalia's conservative preflight
counter; they do not alter provider sampling or model selection.

```json
{
  "id": "codex-default",
  "label": "Codex",
  "tokenizer_encoding": "o200k_base",
  "token_safety_multiplier": 1.0
}
```

Generation admission, background maintenance, and operator planning all size a
session with the counter of the model that session will next use, so these
values are one authority rather than three. A summary and its checkpoint record
the counter that produced them, so a later counter change is detectable rather
than silently redefining what "enough coverage" means: the checkpoint is not
reused across a change, and `context-plan` reports `counter_changed` and stops
asserting readiness it can no longer prove. Records written before this was
tracked carry no identity and are treated as unknown, not as a mismatch, so
existing checkpoints survive the upgrade.

A model may declare `context_window_tokens`, the total window it accepts:

```json
{
  "id": "small-window-model",
  "label": "Small window",
  "context_window_tokens": 32000
}
```

`target_provider_input_tokens` is a per-project *intent*. When the selected model
declares a smaller window, the effective input ceiling narrows to
`context_window_tokens - output_reserve_tokens` for that request, and admission,
maintenance planning, and operator reporting all read the narrowed budget. A
window that cannot satisfy the project's own floors is refused as a typed
oversized-context outcome before the provider is launched, rather than becoming
an opaque provider error. Omitting the field leaves the model unconstrained, so
existing catalogs behave exactly as before.

`MARGINALIA_CONTEXT_MAINTENANCE_MODEL` names a configured model used only to
derive long-session summaries. The household rollout uses
`claude-context-summary` (provider `claude-code-local`, upstream `sonnet`) with
`purpose` set to `context-maintenance`. It must be explicitly available in the
private provider catalog; Marginalia never substitutes it for the writer's
selected model.

Bounded context is disabled per project until an operator prebuilds and
validates every required summary, then activates it. Maintenance calls use an
isolated Agent Governor context and do not select, retry, or fail over the
writing model. See [OPERATIONS.md](OPERATIONS.md) for rollout commands and
[RELIABILITY.md](RELIABILITY.md) for the token and finality invariants.
