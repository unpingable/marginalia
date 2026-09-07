# Gate 3A qualification checkpoint

Run identity: `marginalia-gate3a-20260907-001`

Status: durable guest qualification producer running.

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
- Guest producer unit: `marginalia-gate3a-producer.service`
- Guest producer invocation: `01975bbc6b1844019f71270f5330880d`
- Guest producer PID at launch: `3081`
- Local VM state: `.gate3/qualification-vm/marginalia-gate3a-20260907-001/`
- Guest source roots: `/srv/qualification/ag_ng` and `/srv/qualification/docket`
- Host log: `.gate3/qualification-vm/marginalia-gate3a-20260907-001/serial.log`
- Guest results: `/srv/qualification/results/`
- Host-copied results: `.gate3/qualification-vm/marginalia-gate3a-20260907-001/results/`

## Guest capability remediation

- Rust: `rustc 1.94.0 (4a4ef493e 2026-03-02)`, installed with rustup's minimal profile.
- Bubblewrap: Ubuntu package `0.9.0`.
- User namespaces: `kernel.unprivileged_userns_clone=1`, `user.max_user_namespaces=23548`.
- Ubuntu's restricted-userns policy initially moved `/usr/bin/bwrap` to the generic `unprivileged_userns` profile, which denied the namespace's required capabilities.
- The disposable VM alone now carries an executable-specific `userns` profile derived from ag-ng commit `746d3919`; profile SHA-256 `dc79c682840565e473b7c478eb8acdcd53579efd9d76bab5164740f01045f57c`.
- The production host's namespace and AppArmor settings were not changed.
- The real Bubblewrap probe passed after the guest profile was loaded.

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
3. Inspect the existing guest producer with `sudo systemctl status marginalia-gate3a-producer.service`; do not restart it while active.
4. Connect only through the recorded loopback SSH endpoint and key in the local VM state directory.
5. Inspect existing guest result/status files before taking any action.
6. If the producer is still running, resume bounded monitoring without restarting it.
7. If the VM stopped, classify the existing evidence before deciding whether the run is failed or indeterminate.
8. The next authorized action after a passing `RESULT` is to seal the Gate 3A evidence and begin 3B. A contract/product defect or unresolved qualification failure stops the campaign.

Worker recovery does not imply recovery of an interrupted external provider execution.
