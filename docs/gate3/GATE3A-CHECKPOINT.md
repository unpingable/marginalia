# Gate 3A qualification checkpoint

Run identity: `marginalia-gate3a-20260907-001`

Status: VM running; cloud-init/toolchain provisioning in progress.

## Frozen inputs

- Marginalia coordination repository: `b381cff46f115f361aaf71f89a1c013ad50e8cff`
- ag-ng source: `cb85d363e2495a75f78c28fb8ce9b46af1f289c0`
- Docket source: `c49ad8d0f26fb2a13b9dbafdde84d7abfe1f867b`
- Ubuntu source image: `/data/git/ag_ng/.campaign-local/gcl-v1/downloads/ubuntu-24.04-server-cloudimg-amd64.img`
- Ubuntu source image SHA-256: `d0fe84bb5f80853425fa6be28e2c106f30104c3cfe8611933f2e65c9b63f0e30`
- QEMU executable: `/usr/bin/qemu-system-x86_64`
- QEMU version: `8.2.2 (Debian 1:8.2.2+ds-0ubuntu1.18)`
- QEMU SHA-256: `8a35ccba41582fc6c38b9df85fc9e35fa1d42f414d2d7d8090ee9b2f5e7c0854`

## Execution identity and locations

- Host: local KVM host (`jbeck`, member of `kvm`)
- Durable systemd unit: `marginalia-gate3a-20260907-001.service`
- systemd invocation: `529ddcbb87134a2abd971dc1be4fc02b`
- QEMU main PID at launch: `1916554`
- SSH endpoint: `127.0.0.1:23042`, user `qualify`, run-specific identity
- Local VM state: `.gate3/qualification-vm/marginalia-gate3a-20260907-001/`
- Guest source roots: `/srv/qualification/ag_ng` and `/srv/qualification/docket`
- Host log: `.gate3/qualification-vm/marginalia-gate3a-20260907-001/serial.log`
- Guest results: `/srv/qualification/results/`
- Host-copied results: `.gate3/qualification-vm/marginalia-gate3a-20260907-001/results/`

## Expected terminal records

- `environment.txt`: OS, kernel, CPU, memory, user-namespace and Bubblewrap probes.
- `toolchain.txt`: compiler, Cargo, Git, Bubblewrap, SQLite, and package identities.
- `sources.sha256`: exact Git commit/tree and Cargo lockfile identities.
- `ag-ng-suite.log` and `.status`.
- `docket-suite.log` and `.status`.
- `cross-process.log` and `.status`.
- `docket-reproduction.log` and `.status`.
- `RESULT`: one unambiguous Gate 3A result.

## Resume procedure

1. Read this checkpoint; do not start a replacement VM or duplicate suite.
2. Inspect `systemctl --user status marginalia-gate3a-20260907-001.service` and the QMP/serial records.
3. Connect only through the recorded loopback SSH endpoint and key in the local VM state directory.
4. Inspect existing guest result/status files before taking any action.
5. If the producer is still running, resume bounded monitoring without restarting it.
6. If the VM stopped, classify the existing evidence before deciding whether the run is failed or indeterminate.
7. The next authorized action after a passing `RESULT` is to seal the Gate 3A evidence and begin 3B. A contract/product defect or unresolved qualification failure stops the campaign.

Worker recovery does not imply recovery of an interrupted external provider execution.
