# Marginalia household operations

Marginalia's Compose deployment has two processes built from the same image:

- `marginalia` runs the Agent Governor daemon and writing-room API.
- `marginalia-backup` reads `/data` and performs enabled workspace schedules.

Both use the existing `marginalia_data` volume. The worker mounts that volume
read-only. The host backup path is mounted at `/backups`; no backup operation
rewrites the live data volume.

## Configure the backup destination

Copy `.env.example` to the untracked `.env` and set the host path:

```bash
MARGINALIA_BACKUP_HOST_PATH=/tank/nfs/marginalia
MARGINALIA_BACKUP_REQUIRE_REMOTE=true
MARGINALIA_DEPLOYMENT_ID=household-compose
```

The host must mount the NAS before Compose starts, and the mount must be
writable. Marginalia does not mount or remount NFS and will not fall back to
the container filesystem if `MARGINALIA_BACKUP_REQUIRE_REMOTE=true`. The API
reads Linux mount metadata and refuses backup creation unless `/backups` is on
NFS, CIFS/SMB, or SSHFS. Confirm the host mount first:
the host:

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
- `/health/ready` requires the AG contract/provider and durable records to be
  ready; it returns HTTP 503 otherwise.
- `/health` retains the concise runtime/provider report.
- `/v1/system` reports application version, image/build/deployment identity,
  supported schemas, preflight results, and backup destination state.

The Docker health check uses `/health/ready`. Build SHA, build time, image ref,
and deployment ID are also stored in every backup manifest.

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
  --volume /tank/nfs/marginalia:/backups:ro \
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
```

An enabled schedule that cannot write logs a failed `backup_attempt` and keeps
polling. It never disables retention, changes application behavior, or writes
to `/data`.
