# Generation evidence deployment specification

This uses existing Marginalia, Compose, NAS, and operator mechanisms. It does
not create a separate security platform.

## Process ownership and readers

- `marginalia-generation` is the only process that writes provider response
  evidence. It receives the authorization issuer, providerctl identity, and
  evidence keyring, but no provider API credential.
- `marginalia-providerd` receives its RPC identity, provider API credential
  files, and optional command-provider auth volume. It cannot read Marginalia's
  evidence keyring or writing state.
- `marginalia` receives the model catalog and evidence keyring so it can
  reconcile and accept an exact candidate. It receives no issuer, RPC private
  key, provider API credential, or provider login state.
- `marginalia-backup` can copy encrypted evidence from its read-only data mount
  but receives no keyring.

No API returns raw evidence. Candidate inspection exposes lifecycle facts and
digests; story content appears only through application acceptance.

## Store and audit

Encrypted AES-256-GCM envelopes live below each project's
`marginalia/generation-evidence/blobs/` directory. The associated access log
records body-free write, read, refused-expired-read, and purge events. Provider
request and response capture excludes process environments, credential files,
authorization headers, and provider login state. The captured body begins after
ag-providerd has applied credentials and contains only the request/result
material needed for custody and reconciliation.

## Keys and recovery

The closed v1 keyring records an active key ID and versioned keys. Blob metadata
records the key ID. Rotation is additive: retain old versions until every
matching live body and retained backup has expired.

Keys are never below `MARGINALIA_DATA_ROOT`, in Git, in project exports, or in a
workspace archive. On this installation they live under the owner-only NAS
recovery hierarchy. Entrypoints copy a bind-mounted source into a root-owned
`0600` file inside each authorized container.

A backup is not evidence-recoverable merely because checksums pass. Restore a
ciphertext sample into an isolated root, separately supply the matching NAS
keyring, and prove decryption. Record the key ID used without recording key
bytes. Losing that key makes retained ciphertext unrecoverable.

## Expiry and backups

Live bodies expire after `MARGINALIA_EVIDENCE_RETENTION_DAYS` (30 by default).
Purge removes the live encrypted envelope and retains non-secret candidate,
digest, attempt, and audit facts. It cannot remove ciphertext already copied
into a retained backup. Key-version retention therefore covers the longer of
live evidence retention and backup retention.

Workspace backup uses SQLite's online backup API and copies encrypted blobs and
audit records, never key material. Restore exercises both the application
archive and separately keyed evidence decryption.
