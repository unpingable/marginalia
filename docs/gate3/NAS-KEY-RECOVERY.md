# Gate 3 key recovery custody

The production recovery set is stored outside Git at
`/tank/nfs/marginalia/recovery-secrets/gate3-109a0ef/` on the NFS NAS. The parent,
release, and key files are owner-only (`0700` directories and `0600` files).
The two copied files were byte-verified against the separately held host recovery
copy on 2026-09-07.

Recovery was exercised, not inferred: a harmless fixture was encrypted with the
deployed production evidence key, its root-owned ciphertext was copied into an
isolated restore directory, and the restored sample was decrypted successfully
using only the NAS keyring mounted read-only into the deployed image.

The NAS is a separate host failure domain, not a geographically off-site vault.
Its NFS export uses host/UID authorization (`sec=sys`), so directory permissions
and NAS access control are part of the recovery boundary. Key material must never
be committed. Evidence-body expiry in the live store does not erase ciphertext
from any retained backup; backup retention and key-version retention must remain
aligned.
