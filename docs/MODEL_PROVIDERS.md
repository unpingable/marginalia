# Model providers under ag-ng

Marginalia's web process never calls a model. It freezes work in the generation
store; a Docket worker presents exact authorization through `ag-providerctl`;
credential-isolated `ag-providerd` performs the HTTP or command dispatch.

## Files and ownership

Keep deployment inputs outside Git. On this installation use the NAS:

```text
/tank/nfs/marginalia/ag-ng/production/
├── config/
│   ├── providers.json
│   ├── providerd.toml
│   └── providerctl.toml
└── secrets/
    ├── marginalia-ag-issuer.pk8
    ├── marginalia-evidence-keys.json
    ├── provider-identities.json
    ├── providerctl/rpc.pk8
    └── providerd/
        ├── rpc.pk8
        └── <provider API credential files>
```

Directories must be `0700`; files must be `0600`. NAS ownership need not be
root: each container entrypoint validates the mode and copies only its allowed
inputs to root-owned private paths. The web receives the catalog and evidence
keyring; the worker receives issuer/evidence/providerctl keys; only providerd
receives provider API credentials and command-provider login state.

## Provider catalog

`providers.json` is strict versioned JSON. This hosted example demonstrates the
shape; replace model names, context ceilings, and prices with values you have
actually configured or verified:

```json
{
  "version": 1,
  "default_model": "writer",
  "providers": [
    {
      "id": "openrouter",
      "protocol": "openai-compatible",
      "inference": "hosted",
      "base_url": "https://openrouter.ai/api/v1",
      "api_key_env": "OPENROUTER_API_KEY",
      "connect_timeout_seconds": 10,
      "read_timeout_seconds": 1800,
      "timeout_seconds": 1800,
      "models": [
        {
          "id": "writer",
          "model": "provider/model-name",
          "label": "Writer",
          "purpose": "writing",
          "context_window_tokens": 32768,
          "max_output_tokens": 4096,
          "token_safety_multiplier": 1.1,
          "pricing": {
            "input_per_million_usd": 0,
            "output_per_million_usd": 0
          }
        }
      ]
    }
  ]
}
```

The `api_key_env` value is an identifier only; its value is never read into the
generated policy. It becomes a normalized credential filename. The example
above expects `secrets/providerd/openrouter-api-key`.

Supported protocols are:

| Protocol | Dispatch | Credential location |
|---|---|---|
| `openai-compatible` | credentialed HTTPS, or allowlisted HTTP only when `inference` is `local` | required for HTTPS; absent for local HTTP |
| `anthropic-messages` | HTTPS Anthropic Messages | required providerd file |
| `existing-command` | image-provided Codex command | providerd auth volume |
| `local-command` | configured Claude Code or Kimi Code executable | providerd auth volume |

Generated ag-ng policy uses the incompatible, explicit
`ag.config.providerd.v2` transport shape. Remote credentials cannot be
omitted. Local HTTP records the exact configured origin in an operator-owned
allowlist and denies redirects. Command routes name a fixed executable,
working directory, built-in adapter, and closed environment; prompts never
become shell or caller-selected argv.

`purpose` is `writing` or `context-maintenance`. The catalog default must be a
writing model. Model IDs are stable application choices; upstream model names
are separate.

An operator may retain a temporarily unsupported selection in the writer-facing
catalog without enrolling a dispatch route:

```json
{
  "id": "kimi-k3",
  "model": "kimi-k3",
  "label": "Kimi K3",
  "availability": "unavailable",
  "unavailable_reason": "Configured model is unavailable for this account."
}
```

The reason is bounded, printable operator text and must not contain credential
material. The selection remains visible and disabled; a direct API request is
refused before authorization or dispatch. Removing the provider from the
catalog is not the mechanism for reporting temporary unavailability.

Configured context ceilings, discovery evidence, and observed successful prompt
sizes are different facts. A successful prompt establishes only a tested lower
bound. Never describe it as the provider maximum.

`max_output_tokens` is the configured HTTP-provider output ceiling (default 4,096)
and becomes part of the immutable physical-dispatch request.
Command adapters have their own fixed arguments and may not expose a token cap;
their bounded process deadline and captured-byte ceiling are therefore reported
as backend limitations rather than mislabeled as a token limit.

## Create identities and policy

Create the catalog first, then use the exact candidate image:

```bash
docker run --rm \
  -v /tank/nfs/marginalia/ag-ng/production:/custody \
  --entrypoint marginalia-generation-secrets IMAGE \
  /custody/secrets

docker run --rm \
  -v /tank/nfs/marginalia/ag-ng/production:/custody \
  --entrypoint marginalia-ag-provider-config IMAGE \
  /custody/config/providers.json \
  /custody/secrets/provider-identities.json \
  /custody/config
```

The generators refuse partial replacement. Do not regenerate identities during
an update: pending work and evidence are bound to them. Back up the keys
separately and record key versions.

For a `local-command` catalog, pass the executable and working-directory
environment variables named in that catalog while generating policy. Paths in
generated policy must be container paths, such as `/opt/claude/claude` and
`/var/lib/marginalia/provider-work`, never host paths.

Run both parsers before activation:

```bash
docker run --rm \
  -v /tank/nfs/marginalia/ag-ng/production/config:/run/input:ro \
  --entrypoint /bin/bash IMAGE -lc \
  'install -D -o root -g root -m 0600 /run/input/providerd.toml /tmp/providerd.toml && ag-providerd --check-config --config /tmp/providerd.toml'
```

The deployment qualification also performs a real `providerctl`/`providerd`
cross-process request. It must generate the actual production-shaped catalog,
load it using the exact candidate daemon, preserve every provider selection,
and exercise credentialed HTTPS, local HTTP, and command-route fixtures.
Parser success alone is insufficient.

## Writer-facing selection and status

Configured writing models appear in the conversation selector grouped by local
or hosted model and local or subscription agent. The configured selection and
route are frozen into each dispatch and displayed with the result. They are not
promoted into physical-execution evidence. Provider-returned model identity is
recorded separately when present. Command tools that do not attest the physical
provider/model are displayed as **Observed provider/model unavailable**, even
though their configured selection remains known.

Usage is reported as provider-reported, normalized, or unavailable. Cost is:

- `known` zero for local inference, excluding electricity and hardware;
- `estimated` only when token usage and configured rates exist;
- `unavailable` otherwise.

These labels are observational. They do not change admission or retry policy.

## Project dispatch switch

Erin can find the switch in two places: the persistent header button labeled
**Generation enabled** or **Generation paused**, and the first control under
**Project direction → Generation**. Off means generation is paused: the composer
prevents submission and presents an
authorized enable control. It does not cancel, abandon, hide,
or synchronously reroute work already in custody.
