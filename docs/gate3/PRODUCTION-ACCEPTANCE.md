# Gate 3 production acceptance

Gate 3 was promoted to Erin's production stack on `2026-09-07` and accepted at
`2026-09-07T17:39:45Z`.

## Exact release

- Candidate commit: `109a0ef8c612f42f83be5376f6f585350e8ea796`
- Image: `marginalia:gate3-109a0ef`
- Image ID: `sha256:048973fba14e25915d8170f336bf87e3949ecc58c8d34e0329c34c4c5f3c0fe9`
- Test-instance acceptance record commit: `b40b933751e9f0254f3147e72c4988fe49375d06`

The image was built from the frozen candidate, independently qualified, and
deployed with Compose builds disabled. The later acceptance records contain no
executable changes.

## Backup and recovery boundary

Before deployment, workspace backup
`marginalia-erin-20260907T173110391271Z.zip` was created with SHA-256
`4b73eb3279bfd1a60495c17130c568c6eab2280708ccd448a850be127af74a79`.
Its outer checksum and members verified, and an isolated restore test loaded all
seven sessions and 747 messages with no untracked session files.

Production generation signing and evidence keys were generated once outside
the application data and NFS backup roots. Live files are root-owned mode 0600
below a mode-0700 directory. An identical mode-0600 recovery copy is retained
in a separate operator-controlled directory outside the application tree. Key
material and plaintext values were not recorded in deployment logs or this
repository.

## Deployment checks

- Web, generation worker, backup worker, and synthetic worker all run the exact
  image ID above.
- The web process is healthy; worker health checks are intentionally disabled by
  the supported Compose configuration.
- Migration readiness passed with no migration required or applied.
- The production backup target remains writable NFS and satisfies the remote
  backup requirement.
- The governed Codex backend reports connected and available.
- Web and generation worker resolve the same shared governor Unix socket.
- The backup worker has no generation-key mount; web and generation worker have
  the key directory mounted read-only.
- The Generation reliability controls are present in the served production UI.
- Durable generation is deployment-available but remains disabled in Erin's
  default project until she enables **Project direction → Generation
  reliability → Use durable generation for this project**.
- The post-deployment error/timeout log audit found no runtime failure.

The first Compose `--wait` invocation returned nonzero because the backup worker
intentionally has no health check, after all services had started. Direct state,
image, API, mount, socket, and log checks established acceptance. A custom
production wiring omission was also caught before project activation: the web
service initially used its legacy governor socket location. The production
Compose file was corrected to bind web and worker to
`/run/marginalia/governor.sock`, the web service was recreated, and the shared
socket was then proven from both containers.

The previous production environment and Compose files are retained as
permission-matched pre-Gate-3 copies for rollback. Previous-image read/write
compatibility was qualified before this release; a rollback must still operate
on a copied or freshly backed-up production state, never the sole live volume.
