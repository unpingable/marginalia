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
    }
  ]
}
```

Provider IDs and configured model IDs must be unique. Configured model IDs are
the values selected by the UI and passed through the governed daemon contract.
The nested `model` value is the exact upstream OpenAI-compatible model ID.
An `existing-command` model without a nested `model` delegates to the
existing command's default model behavior.

Only `http` and `https` base URLs without embedded credentials, query
strings, or fragments are accepted. `api_key_env` contains an environment
variable name, never a credential. Unknown fields, protocols, models, duplicate
IDs, malformed URLs, and missing required credentials fail visibly.

## Behavior

The model picker lists only models in this file. Selection is stored on the
conversation and affects future generation only. Each newly persisted assistant
message records the configured provider ID and exact upstream model ID.
Historical messages that predate these fields remain readable and display their
identity as unrecorded.

Marginalia does not discover provider models, route requests automatically,
silently fail over, or substitute another configured model after a failure.

The OpenAI-compatible transport supports system, user, and assistant messages,
non-streaming completions, streaming SSE, bounded timeouts, and cancellation.
The currently qualified Agent Governor Codex-command boundary flattens governed
conversation messages before invoking its configured command, so live
command-dispatched providers retain the same prompt-shaping semantics as the
existing Codex backend.

## Deployment wiring

The deployment must mount the JSON file read-only, set
`MARGINALIA_MODEL_CONFIG` to its container path, and pass only the credential
environment variables referenced by enabled providers. Linux deployments that
need a container-to-host endpoint may add a host-gateway mapping in their
private Compose override.

Do not place deployment endpoints, credential values, enabled household models,
or local defaults in source-controlled Compose files.
