# Durable generation operations

Durable generation is an opt-in compose profile. Production remains on the
synchronous path until an operator deliberately enables the profile and marks
the worker available. Project settings then expose the writer-facing switch at
the top of **Project direction → Generation reliability**.

## Exact runtime

The image builds `ag-loopctl` from ag-ng commit
`cb85d363e2495a75f78c28fb8ce9b46af1f289c0` and `docket` from Docket commit
`c49ad8d0f26fb2a13b9dbafdde84d7abfe1f867b`. `sync-deps.sh` exports those Git
objects and does not copy either checkout's branch or uncommitted files. Image
labels and `/app/*_CONTRACT_COMMIT` record both identities.

The web process owns provider configuration and starts Agent Governor. The
generation worker shares only its Unix socket through `marginalia_runtime`.
Both processes share the data volume because Docket custody, encrypted evidence,
and application acceptance records must survive either container restarting.

## One-time activation

Create a host directory outside the Marginalia data and backup roots. Generate
the two secret files once with the exact candidate image:

```text
mkdir -m 700 ./secrets
docker run --rm -v "$PWD/secrets:/secrets" MARGINALIA_IMAGE \
  marginalia-generation-secrets /secrets
```

Copy that directory to a separately controlled recovery location. Do not put it
inside the workspace backup destination. Configure:

```text
COMPOSE_PROFILES=durable-generation
MARGINALIA_DURABLE_GENERATION_AVAILABLE=true
MARGINALIA_GENERATION_SECRETS_HOST_PATH=./secrets
```

Start the candidate normally. Confirm both `marginalia` and
`marginalia-generation` are running before enabling the project switch. The
availability flag is a deployment assertion, not a worker health inference; do
not set it when the worker profile is absent.

## Kill switch and reconciliation

Clear the project checkbox and save to stop new durable dispatches. Inspection,
evidence recovery, reconciliation, and acceptance of already-dispatched work
remain available. Do not stop the worker merely to disable new work. An unknown
provider outcome remains unknown until exact response evidence is recovered or
the provider establishes a qualified terminal result. Browser wait cancellation
and confirmed provider cancellation do not prove that no execution or billing
occurred.

## Recovery and retention

Live response bodies are encrypted with the versioned evidence keyring and are
purged by the generation worker after the configured retention interval.
Lifecycle facts and digests remain. Ciphertext already copied into a retained
backup remains for that backup's retention period.

An evidence restore test must use an isolated restored data root and the
separately supplied keyring. Successful archive checksum verification without a
successful evidence decryption is not evidence-response recovery. Keep old key
versions for at least the longer of live evidence retention and backup
retention.

## Rollback qualification

Before production acceptance, write representative state with the candidate,
stop it cleanly, start the previous accepted image against a copy of that state,
and perform both reads and writes. Then return to the candidate and verify the
new writes. The generation tables are additive and ignored by the previous
image, but that claim is accepted only after this exact rehearsal. Never run a
previous image against the sole copy of upgraded production state.
