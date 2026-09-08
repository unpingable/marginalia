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
| `openai-compatible` | HTTPS, or HTTP only when `inference` is `local` | optional providerd file |
| `anthropic-messages` | HTTPS Anthropic Messages | required providerd file |
| `existing-command` | image-provided Codex command | providerd auth volume |
| `local-command` | configured Claude Code or Kimi Code executable | providerd auth volume |

`purpose` is `writing` or `context-maintenance`. The catalog default must be a
writing model. Model IDs are stable application choices; upstream model names
are separate.

Configured context ceilings, discovery evidence, and observed successful prompt
sizes are different facts. A successful prompt establishes only a tested lower
bound. Never describe it as the provider maximum.

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
cross-process request; parser success alone is insufficient.

## Writer-facing selection and status

Configured writing models appear in the conversation selector grouped by local
or hosted model and local or subscription agent. The selected actual model and
route are frozen into each dispatch and displayed with the result.

Usage is reported as provider-reported, normalized, or unavailable. Cost is:

- `known` zero for local inference, excluding electricity and hardware;
- `estimated` only when token usage and configured rates exist;
- `unavailable` otherwise.

These labels are observational. They do not change admission or retry policy.

## Project dispatch switch

Erin can find the switch in two places: the persistent header button labeled
**Generation enabled** or **Generation paused**, and the first control under **Project direction → Generation
reliability**. Off means stop new dispatches. It does not cancel, abandon, hide,
or synchronously reroute work already in custody.
