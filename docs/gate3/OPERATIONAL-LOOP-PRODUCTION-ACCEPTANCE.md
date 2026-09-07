# Operational-loop production acceptance

The operational-loop candidate was promoted to Erin's production stack and
accepted on `2026-09-07` at `2026-09-07T21:02:59Z`.

## Exact release

- Candidate commit: `58decafc9a55a231041abbf01d165f357a0e78af`
- Candidate tree: `541c97f062a3f88764f7dac7cf1020c0877912b3`
- Test-instance acceptance record: `389a645d53634cb71c39d6faae2844a0b846e840`
- Image: `marginalia:opsloop-58decaf`
- Image ID: `sha256:e863c7a99e1aa97c92bc33ce3634ac4ed5af2dab429da64c3b3e6c7c2dadd1e8`

The image was built from the frozen and pushed candidate, qualified in the
isolated test instance, and deployed with Compose builds disabled. This later
record contains no executable change and is not part of the deployed image.

## Backup

Before replacement, production created workspace backup
`marginalia-erin-20260907T205934295387Z.zip` with SHA-256
`36e2d6b4f2088593044e9335f2197ec0a85587950276ba10b65d874fde76128f`.
The archive and outer checksum verified. Its isolated restore rehearsal loaded
7 sessions, 747 messages, 2 artifacts, and 133 canon reviews, with no untracked
session files.

The backup volume remains the NFS v4 export at
`192.168.69.10:/tank/nfs/marginalia`. Separately recoverable production signing
and evidence keys remain outside Git at the owner-only NAS location documented
in `NAS-KEY-RECOVERY.md`.

## Deployment acceptance

- Web, generation, backup, and synthetic services all run the exact image ID.
- The web service is healthy; its deployment metadata reports the candidate
  commit and image reference.
- Migration readiness passed with no migration required or applied.
- Production retained 2 projects, 7 sessions, and 747 messages.
- Web and generation worker see the same governor Unix socket identity.
- Web and generation worker mount generation keys read-only. The backup and
  synthetic workers have no generation-key mount; backup reads application data
  read-only.
- The served UI contains the prominent **Generation reliability** control and
  its **What does this change?** explanation.
- Durable generation remains deployment-available and disabled for Erin's
  project until she chooses to enable it. Deployment did not silently alter the
  writer's per-project choice.
- The bounded post-deployment audit found no exception, timeout, failed event,
  or error-level record in any of the four service logs.

The prior Compose environment remains in the permission-matched file
`.env.pre-opsloop-20260907T2059Z`. Rollback must begin from a fresh backup or a
copy of production state. Previous-image read/write compatibility and candidate
forward recovery were qualified before this deployment.
