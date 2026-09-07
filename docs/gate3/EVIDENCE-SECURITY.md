# Generation evidence deployment specification

This specification applies only to the durable-generation feature. It uses the
existing Marginalia process, volume, backup, and operator mechanisms; it does
not introduce a separate security platform.

## Ownership and access

- The `marginalia-generation` process is the only process that creates provider
  response blobs. It writes authenticated AES-256-GCM envelopes below the
  selected project's `<context>/marginalia/generation-evidence/blobs/` directory.
- The `marginalia` web process reads a blob only while reconciling or accepting
  its exact candidate. Every read, refused expired read, write, and purge is
  appended to `generation-evidence/access.jsonl` without response content.
- The backup worker can read the data volume and therefore copies ciphertext,
  but it is not given the keyring and cannot decrypt response bodies.
- Application API authentication and loopback binding remain the read-access
  boundary. No endpoint returns raw evidence; candidate inspection returns
  lifecycle facts, digests, and the accepted result only through the existing
  authenticated writing-room response.

## Keys

The application and generation worker receive one read-only keyring file at
the configured `MARGINALIA_EVIDENCE_KEY_FILE`. Its closed v1 document records
an active key ID and one or more 32-byte keys. Key IDs are stored with every
blob, so rotation is additive: retain old key versions until every corresponding
live body and retained backup has expired.

The keyring is never stored below `MARGINALIA_DATA_ROOT`, never included in a
workspace archive, and must be mode 0600 or stricter. Deployment owns a separate
recoverable copy. A backup is not declared restorable until the archive is
restored into an isolated root and its ciphertext is decrypted with that
separately supplied keyring. Losing the keyring makes retained ciphertext
unrecoverable; restoring a data volume alone must not be reported as response
recovery.

Compose mounts the host generation-secret directory read-only into the web and
generation-worker containers. The backup container receives neither that mount
nor the keyring. The current appliance runs both readers as the container's root
identity; host file mode 0600 and local Docker administration are therefore the
concrete access boundary. Key creation is one-shot and refuses to replace either
existing file.

## Retention and backup consequence

Live response bodies expire after the configured retention interval (30 days
by default). Purging deletes the live encrypted envelope and records the purge;
the candidate identity, response digest, attempt identity, and lifecycle facts
remain. Live expiry does not delete ciphertext already captured in retained
workspace archives. Those copies remain decryptable for the archive's full
retention period, so key retention must cover the longer of evidence retention
and backup retention.

Workspace backup uses SQLite's online backup API for each live database and
does not copy WAL/SHM files independently. It copies encrypted evidence blobs
and access records, but never key material. Archive integrity and application
restore checks are necessary but not sufficient for evidence recovery; the
separately keyed decryption rehearsal is the final evidence-specific check.
