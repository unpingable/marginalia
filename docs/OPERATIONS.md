# Marginalia household operations

Marginalia's Compose deployment has three processes built from the same image:

- `marginalia` runs the Agent Governor daemon and writing-room API.
- `marginalia-backup` reads `/data` and performs enabled workspace schedules.
- `marginalia-synthetic` performs bounded isolated governor/provider probes.

Both use the existing `marginalia_data` volume. The worker mounts that volume
read-only. A Docker-managed NFS volume (recommended) or host bind is mounted at
`/backups`; no backup operation rewrites the live data volume.

## Configure the backup destination

Copy `.env.example` to the untracked `.env` and configure the NFS export:

```bash
MARGINALIA_BACKUP_NFS_HOST=192.168.69.10
MARGINALIA_BACKUP_NFS_EXPORT=/tank/nfs/marginalia
MARGINALIA_BACKUP_NFS_VERSION=4
MARGINALIA_BACKUP_NFS_VOLUME=marginalia_backups_nfs
MARGINALIA_BACKUP_REQUIRE_REMOTE=true
MARGINALIA_DEPLOYMENT_ID=household-compose
```

The start scripts automatically include `docker-compose.nfs.yml` when either
NFS variable is set and require both values. Docker mounts that export directly,
so confined Docker daemons do not depend on their view of the host mount
namespace. Marginalia will not fall back to container storage when
`MARGINALIA_BACKUP_REQUIRE_REMOTE=true`; the API reads Linux mount metadata and
refuses backup creation unless `/backups` is on NFS, CIFS/SMB, or SSHFS.

For a conventional Docker installation, a host-mounted NAS remains available
as a fallback: leave the NFS variables unset and set
`MARGINALIA_BACKUP_HOST_PATH=/tank/nfs/marginalia`. Confirm that mount first:

```bash
findmnt -T /tank/nfs/marginalia
test -d /tank/nfs/marginalia
test -w /tank/nfs/marginalia
```

In the UI, choose a workspace, open **Backups**, and set its schedule, UTC
hour, retention, and NAS subdirectory. A workspace is disabled by default.
Manual **Back up now** works independently of the schedule switch and always
verifies the archive before reporting success.

## Startup and migration safety

Every application start runs this before starting the daemon or web server:

```bash
python3 -m gov_webui.ops \
  --data-root /data \
  --context-id erin-writing \
  preflight --apply-migrations
```

Preflight rejects unreadable records and schema versions newer than the image.
The schema-1-to-2 library migration first writes
`library.pre-schema-2.json`, then atomically writes the new library and a
`library.migration-1-2.json` SHA-256 receipt. It assigns existing content to
the `Erin` workspace; it does not rewrite session, canon, manuscript, or
artifact content.

Inspect the same checks without applying a migration:

```bash
docker compose exec -T marginalia \
  python3 -m gov_webui.ops --data-root /data \
  --context-id erin-writing preflight
```

## Health and operational provenance

- `/health/live` proves the web process is alive.
- `/health/ready` requires the AG contract/provider, governor progress, and
  durable records to be ready; it returns HTTP 503 otherwise.
- `/health` retains the concise runtime/provider report.
- `/v1/system` reports application version, image/build/deployment identity,
  supported schemas, preflight results, and backup destination state.

The Docker health check uses `/health/ready`. Build SHA, build time, image ref,
and deployment ID are also stored in every backup manifest.

## Live updates and incident retention

The current appliance is a single web-and-governor container. Replacing that
container creates a brief service interruption and can turn an in-flight
generation into a correctly typed, retryable transport failure. Do not rebuild
or recreate a household's live container while someone is writing. Announce a
quiet update window, confirm `execution.in_flight` is zero in
`/health/ready`, take and verify a backup, and then perform the replacement.
The zero count is a preflight signal, not a drain lock; there is currently no
zero-downtime handoff.

Full generation diagnostics are written to container logs while the writer sees
only a safe incident ID and failure class. Docker removes those logs when it
removes the old container. Capture the old container's logs before replacement
or configure deployment-level log retention if post-replacement incident
correlation is required. Incident diagnostics are operational metadata and must
never be copied into sessions, canon, artifacts, or model context.

## Manual backup, verification, and restore rehearsal

The UI is the normal path. The equivalent operator commands are:

```bash
docker compose exec -T marginalia \
  python3 -m gov_webui.ops --data-root /data --backup-root /backups \
  --context-id erin-writing backup --workspace-id erin

docker compose exec -T marginalia \
  python3 -m gov_webui.ops --data-root /data --backup-root /backups \
  --context-id erin-writing verify /backups/erin/ARCHIVE.zip

docker compose exec -T marginalia \
  python3 -m gov_webui.ops --data-root /data --backup-root /backups \
  --context-id erin-writing restore-test /backups/erin/ARCHIVE.zip
```

Each ZIP contains a workspace-scoped library, all project context files,
project snapshots, and shared evidence/receipt files. `manifest.json` hashes
every member; the adjacent `.sha256` file hashes the complete ZIP. A restore
rehearsal extracts into a temporary empty root, validates every JSON record,
loads real session objects, and refuses a backup missing any enrolled
conversation file.

## Disaster restore without overwriting the old volume

Never restore into the live volume. Create a separate candidate volume and
restore there first:

```bash
docker volume create marginalia_restore_candidate
docker run --rm \
  --volume marginalia_backups_nfs:/backups:ro \
  --volume marginalia_restore_candidate:/restore \
  marginalia:local \
  python3 -m gov_webui.ops --backup-root /backups \
  --context-id erin-writing restore /backups/erin/ARCHIVE.zip \
  --target-data-root /restore

docker run --rm \
  --volume marginalia_restore_candidate:/data \
  marginalia:local \
  python3 -m gov_webui.ops --data-root /data \
  --context-id erin-writing preflight
```

Only after both commands pass should an operator point a temporary Compose
override at `marginalia_restore_candidate`, start it on a different port, and
verify the writing in the browser. Keep the original volume until that
verification and an additional backup both succeed.

## Routine checks

```bash
docker compose ps
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
curl -fsS http://127.0.0.1:8000/v1/system
docker compose logs --tail=100 marginalia-backup
docker compose logs --tail=100 marginalia-synthetic
tail -n 20 /backups/marginalia-synthetics.jsonl
```

An enabled schedule that cannot write logs a failed `backup_attempt` and keeps
polling. It never disables retention, changes application behavior, or writes
to `/data`.

See [RELIABILITY.md](RELIABILITY.md) for exact timeout layers, what each health
signal proves, synthetic cadence, and PASS semantics.

## Non-urgent operational TODOs

- [ ] Enable a `main` branch ruleset that requires the `quality`, `test`, and
  `container` GitHub Actions checks while retaining an explicit, bounded
  administrator/emergency bypass.
- [ ] Route recurring synthetic failures to an external notification. Start
  with the conservative policy “two consecutive synthetic failures notify
  James,” consuming the existing JSONL or container-log interface without
  changing the probe's isolation or cadence.
- [ ] Perform and document a blank-host restore drill from Marginalia backups.
  Rebuild the service on a clean host, verify the restored writing and service
  readiness, retain the original data throughout the drill, and record the
  backup identifier, image identifier, duration, and outcome.

## Bounded-context rollout

Back up and verify the target workspace before activation. Context planning,
validation, deactivation, and activation do not call a provider. Only
`context-build` invokes the dedicated maintenance model.

```bash
docker compose exec -T marginalia python3 -m gov_webui.ops \
  --data-root /data --context-id erin-writing \
  context-plan --project-id PROJECT_ID

docker compose exec -T marginalia python3 -m gov_webui.ops \
  --data-root /data --context-id erin-writing \
  --model-config /path/to/models.json \
  --maintenance-model claude-sonnet-4-20250514 \
  context-build --project-id PROJECT_ID

docker compose exec -T marginalia python3 -m gov_webui.ops \
  --data-root /data --context-id erin-writing \
  context-validate --project-id PROJECT_ID

docker compose exec -T marginalia python3 -m gov_webui.ops \
  --data-root /data --context-id erin-writing \
  context-activate --project-id PROJECT_ID
```

The report uses hashed session references and token/message counts, never story
text. Build work is resumable and idempotent: rerunning reuses source-identical
chunks, validated pairwise merges, and already-valid summaries. Activation fails closed unless every
session that currently needs compaction has a source-valid summary covering at
least the required prefix.

Derived files live under:

```text
/data/.governor/CONTEXT/marginalia/context/
├── policy.json
├── SESSION.summary.json
└── SESSION.work.json
```

They are included in verified backups and restore rehearsals, but remain
rebuildable derived/operational state. They are not sessions, canon, artifacts,
search results, manuscript nodes, or authored context.

Rollback is immediate and provider-free:

```bash
docker compose exec -T marginalia python3 -m gov_webui.ops \
  --data-root /data --context-id erin-writing \
  context-deactivate --project-id PROJECT_ID
```

Deactivation leaves durable history and derived files intact. Monitor
content-free `context_allocation` logs (full, summary, recent, prompt, and
predicted-provider token counts), maintenance failures, and provider latency.
Do not raise provider timeouts, delete history, or activate a project whose
validation report is not ready.
