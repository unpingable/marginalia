# Durable generation operations

Durable generation is required in the supported Compose topology. The visible
per-project **Generation enabled/paused** control stops new dispatches only. It never routes
work through classic, and it leaves inspection, reconciliation, evidence
recovery, and acceptance of already-dispatched work available.

## Exact runtime

Every service uses one image built from one committed Marginalia candidate. The
image embeds:

- ag-ng `466dcf2d2dc1ec63ebbde2c7f0b53f2fcf666b95`;
- Docket `181589f910b76030b312d6478bd0ac813a630855`.

The runtime is five services: web, Docket generation worker, ag-providerd,
backup worker, and synthetic probe. Web, worker, and providerd share only the
state or socket each contract requires. Provider credentials and command login
state are visible only to providerd.

## Deployment custody

Set these ignored `.env` values to owner-only NAS directories:

```text
MARGINALIA_AG_NG_CONFIG_HOST_PATH=/tank/nfs/marginalia/ag-ng/production/config
MARGINALIA_GENERATION_SECRETS_HOST_PATH=/tank/nfs/marginalia/ag-ng/production/secrets
```

Use `marginalia-generation-secrets` once, then
`marginalia-ag-provider-config`, as documented in `MODEL_PROVIDERS.md`. The
Compose mounts are read-only inputs. Entrypoints refuse symlinks, missing files,
and group/world-readable modes, then copy permitted files to process-private,
root-owned `0600` paths. Ordinary NAS UID ownership is therefore supported
without weakening host security.

Do not regenerate keys during an update. Record key IDs and keep separately
recoverable versions. Provider API credentials are files below
`secrets/providerd`; they must never be environment variables.

## Reconciliation

An unknown provider outcome stays unknown until exact response evidence or a
provider-supported lookup establishes a result. Browser wait cancellation and
confirmed provider cancellation do not prove non-execution or non-billing. An
exact fetched response begins candidate acceptance; it does not authorize a new
execution.

A worker restart recovers Docket custody and existing evidence. It cannot
necessarily resume a provider HTTP or command generation interrupted before
evidence capture. This distinction is a required crash-test assertion.

## Update and rollback

Before replacement:

1. announce a quiet window and turn off new project dispatches;
2. inspect all non-terminal requests and preserve their custody;
3. take and restore-test a backup;
4. record the currently deployed image digest and all candidate digests;
5. start the qualified candidate digest without regenerating keys.

Rollback compatibility is empirical. Against a copy of state, have the
candidate write representative data, have the previous image read and write it,
then have the candidate read the rollback write. Never point a previous image at
the only copy of upgraded production data. A failed rehearsal blocks deployment;
it does not justify an ad hoc conversion layer.

Any repair after candidate validation creates a new commit and invalidates the
affected checks. Production deployment remains separately approved.
