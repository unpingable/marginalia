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
        }
      ]
    },
    {
      "id": "local-compatible",
      "protocol": "openai-compatible",
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
The nested `model` value is the exact upstream model ID.
An `existing-command` model without a nested `model` delegates to the
existing command's default model behavior.

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
deployment-local. The Kimi Code adapter invokes
one explicit model in noninteractive `stream-json` mode and uses the final
assistant message. The Claude Code adapter invokes `claude --print` with JSON
output, passes the governed prompt over standard input, and returns the result
plus reported token usage. Both adapters apply the configured timeout,
terminate their process group on timeout/cancellation, and normalize command
failures without exposing raw stderr. Local commands currently emit whole
assistant messages rather than token deltas, so Marginalia receives a bounded
completion chunk after each command turn finishes.

For HTTP providers, `timeout_seconds` is a total execution deadline, not merely
a socket timeout. Optional `connect_timeout_seconds` and
`read_timeout_seconds` separately bound connection establishment and response
idle time; each must be no greater than `timeout_seconds`. Continuous SSE data
therefore cannot extend one invocation indefinitely. See
[RELIABILITY.md](RELIABILITY.md) for the outer provider/RPC envelopes.

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

`MARGINALIA_CONTEXT_MAINTENANCE_MODEL` names a configured model used only to
derive long-session summaries. The household rollout uses
`claude-sonnet-4-20250514` (provider `claude-code-local`, upstream
`sonnet`). It must be explicitly available in the private provider catalog;
Marginalia never substitutes it for the writer's selected model.

Bounded context is disabled per project until an operator prebuilds and
validates every required summary, then activates it. Maintenance calls use an
isolated Agent Governor context and do not select, retry, or fail over the
writing model. See [OPERATIONS.md](OPERATIONS.md) for rollout commands and
[RELIABILITY.md](RELIABILITY.md) for the token and finality invariants.
