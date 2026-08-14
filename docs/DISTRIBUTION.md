# Marginalia local-appliance distribution

Marginalia's first supported distribution is an immutable OCI appliance plus
one thin launcher. It is intentionally not a Python-package installation and
does not require users to clone Agent Governor.

## Release contents

The image contains:

- Marginalia `0.1.0`;
- Agent Governor `2.8.1` at
  `e279a94326a0a13dbe43473846b53e4c3a9b31f2` /
  `marginalia-chat-contract-m0`;
- the complete AG Python distribution, including `fiction_governor`;
- receipt-kernel `0.1.0`;
- receipt-v1 `0.1.0`;
- Codex CLI `0.146.1`;
- the aligned AG-daemon/Marginalia entrypoint.

The GitHub release contains:

- `marginalia`, the lifecycle launcher;
- `marginalia.sha256`, its installer-verified checksum;
- `install-marginalia.sh`, the installer that places the launcher in
  `~/.local/bin` and starts it.

The release workflow rewrites the launcher's default image from the human
version tag to the exact multi-architecture manifest digest produced by that
run. The installed launcher therefore pulls an immutable image even if a
version tag is later moved. `MARGINALIA_IMAGE` remains an explicit development
override.

The launcher binds the writing room to loopback, stores writing and Codex login
state in separate named volumes, and never deletes those volumes during
stop/update operations.

## First publication

Publication is deliberately external to an ordinary source commit:

1. Push the reviewed `marginalia-m1` history.
2. Change the GitHub default branch from `marginalia-m0` to the reviewed product
   branch without rewriting either branch.
3. Tag the reviewed release commit `v0.1.0` and push the tag.
4. Confirm the `Publish Marginalia appliance` workflow passes and publishes
   `linux/amd64` and `linux/arm64` manifests.
5. Make the newly created `ghcr.io/unpingable/marginalia` package public. GHCR
   package visibility is a repository-owner setting and cannot be proven by a
   source-only implementation.
6. From an unauthenticated clean host, verify:

   ```bash
   docker manifest inspect ghcr.io/unpingable/marginalia:0.1.0
   curl -fsSL https://github.com/unpingable/marginalia/releases/download/v0.1.0/install-marginalia.sh | sh
   ```

Do not publish a release from the old default M0 branch or reuse the Phosphor
image name.

## Clean-machine acceptance

The release gate is:

```text
install launcher
→ pull exact appliance
→ Codex device login
→ healthy AG/Marginalia boundary
→ browser opens
→ create project and conversation
→ governed response
→ stop/start
→ project and conversation persist
```

No step may require an AG checkout, Python virtual environment, Compose file,
provider environment variable, or editing configuration by hand.

## Provider authority

The launcher configures only process prerequisites and starts the appliance.
AG remains the authoritative provider owner. Marginalia reports the provider
AG actually uses; it does not resurrect the historical local model switch.

Codex authentication lives in `marginalia_codex_home`. Writing state lives in
`marginalia_data`. Neither belongs in project export. Additional providers
must earn their own clean-machine acceptance path before being advertised as
supported.
