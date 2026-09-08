# SPDX-License-Identifier: Apache-2.0
"""M1.5a local-appliance distribution regressions."""

from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AG_NG_ROOT = Path(
    os.environ.get("MARGINALIA_AG_NG_SOURCE_DIR", REPO_ROOT.parents[1] / "ag_ng")
).resolve()
DOCKET_ROOT = Path(
    os.environ.get(
        "MARGINALIA_DOCKET_SOURCE_DIR",
        REPO_ROOT.parents[1] / "docket-river-clerk-live-docket-executor-prerequisite-v1",
    )
).resolve()


def test_retired_test_inventory_has_an_audited_crosswalk() -> None:
    configuration = runpy.run_path(str(REPO_ROOT / "tests" / "conftest.py"))
    retired = configuration["RETIRED_TEST_MODULES"]
    crosswalk = (REPO_ROOT / "docs" / "ag-ng-migration" / "TEST-COVERAGE-CROSSWALK.md").read_text()

    assert sum(retired.values()) == 418
    for module, baseline_cases in retired.items():
        assert f"`{module}`" in crosswalk
        assert f"{baseline_cases}" in crosswalk


def test_sync_stages_the_complete_qualified_ag_distribution(tmp_path: Path) -> None:
    probe = tmp_path / "marginalia-source"
    probe.mkdir()
    shutil.copy2(REPO_ROOT / "sync-deps.sh", probe / "sync-deps.sh")
    shutil.copy2(REPO_ROOT / "AG_NG_CONTRACT_COMMIT", probe / "AG_NG_CONTRACT_COMMIT")
    shutil.copy2(REPO_ROOT / "DOCKET_CONTRACT_COMMIT", probe / "DOCKET_CONTRACT_COMMIT")
    shutil.copytree(REPO_ROOT / "receipt-v1", probe / "receipt-v1")

    environment = os.environ.copy()
    environment["MARGINALIA_AG_NG_SOURCE_DIR"] = str(AG_NG_ROOT)
    environment["MARGINALIA_DOCKET_SOURCE_DIR"] = str(DOCKET_ROOT)
    subprocess.run(
        ["bash", str(probe / "sync-deps.sh")],
        cwd=probe,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert not (probe / "agent-governor").exists()
    assert not (probe / "receipt-kernel").exists()
    assert (probe / "receipt-v1" / "src" / "receipt_v1" / "__init__.py").is_file()
    assert (probe / "ag-ng" / "AG_NG_CONTRACT_COMMIT").read_text().strip() == (
        REPO_ROOT / "AG_NG_CONTRACT_COMMIT"
    ).read_text().strip()
    assert (probe / "ag-ng" / "crates" / "ag-app" / "Cargo.toml").is_file()
    assert (probe / "docket-runtime" / "DOCKET_CONTRACT_COMMIT").read_text().strip() == (
        REPO_ROOT / "DOCKET_CONTRACT_COMMIT"
    ).read_text().strip()
    assert (probe / "docket-runtime" / "crates" / "gwr-local" / "Cargo.toml").is_file()


def _write_fake_docker(bin_dir: Path) -> Path:
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$MARGINALIA_DOCKER_LOG"
case "${1:-}" in
  info|pull|start|stop|rm|logs) exit 0 ;;
  image)
    [ "${2:-}" = inspect ] && exit 0
    ;;
  volume)
    [ "${2:-}" = inspect ] && exit 0
    ;;
  container)
    if [ "${2:-}" = inspect ]; then
      case "$*" in
        *io.marginalia.managed*) printf 'true\\n'; exit 0 ;;
        *State.Health*) printf 'healthy\\n'; exit 0 ;;
        *State.Status*) printf 'running\\n'; exit 0 ;;
      esac
      [ "${MARGINALIA_FAKE_CONTAINER_PRESENT:-0}" = 1 ] && exit 0
      exit 1
    fi
    ;;
  run)
    case "$*" in
      *'--detach'*) printf 'fake-container-id\\n'; exit 0 ;;
      *'--entrypoint /opt/codex/codex'*) exit 0 ;;
    esac
    ;;
esac
printf 'unexpected docker invocation: %s\\n' "$*" >&2
exit 2
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return docker


def test_legacy_launcher_fails_closed_instead_of_bypassing_ag_ng(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_docker(bin_dir)
    log = tmp_path / "docker.log"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "MARGINALIA_DOCKER_LOG": str(log),
            "MARGINALIA_IMAGE": "marginalia:test",
            "MARGINALIA_CONTAINER": "marginalia-acceptance",
            "MARGINALIA_DATA_VOLUME": "marginalia-test-data",
            "MARGINALIA_CODEX_VOLUME": "marginalia-test-codex",
            "MARGINALIA_PORT": "8123",
            "GOVERNOR_CONTEXT_ID": "installer-acceptance",
            "MARGINALIA_SKIP_PULL": "1",
            "MARGINALIA_NO_OPEN": "1",
            "MARGINALIA_NONINTERACTIVE": "1",
        }
    )

    result = subprocess.run(
        [str(REPO_ROOT / "marginalia"), "start"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "single-container start is retired" in result.stderr
    assert not log.exists()


def test_legacy_launcher_update_fails_closed(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_docker(bin_dir)
    log = tmp_path / "docker.log"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "MARGINALIA_DOCKER_LOG": str(log),
            "MARGINALIA_IMAGE": "marginalia:test",
            "MARGINALIA_CONTAINER": "marginalia-acceptance",
            "MARGINALIA_DATA_VOLUME": "marginalia-test-data",
            "MARGINALIA_CODEX_VOLUME": "marginalia-test-codex",
            "MARGINALIA_PORT": "8123",
            "GOVERNOR_CONTEXT_ID": "installer-acceptance",
            "MARGINALIA_NO_OPEN": "1",
            "MARGINALIA_NONINTERACTIVE": "1",
            "MARGINALIA_FAKE_CONTAINER_PRESENT": "1",
        }
    )

    result = subprocess.run(
        [str(REPO_ROOT / "marginalia"), "update", "--no-pull", "--no-open"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "single-container update is retired" in result.stderr
    assert not log.exists()


def test_classic_single_container_installer_fails_closed() -> None:
    result = subprocess.run(
        ["sh", str(REPO_ROOT / "install-marginalia.sh")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "single-container installer is retired" in result.stderr


def test_release_contract_names_and_pins_the_complete_marginalia_appliance() -> None:
    workflow = (REPO_ROOT / ".github/workflows/publish-image.yml").read_text()
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    codex_compose = (REPO_ROOT / "docker-compose.codex.yml").read_text()
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]

    assert "github.repository_owner }}/marginalia" in workflow
    assert "repository: unpingable/agent_governor" not in workflow
    assert "linux/amd64,linux/arm64" in workflow
    assert "docker/setup-qemu-action@v3" in workflow
    assert "repository: unpingable/ag_ng" in workflow
    assert "ref: 1bd4225a94fa82df066923612f713a93ba93a7bb" in workflow
    assert "repository: unpingable/docket" in workflow
    assert "ref: 181589f910b76030b312d6478bd0ac813a630855" in workflow
    assert "./sync-deps.sh" in workflow
    assert "IMAGE_NAME: ${{ github.repository_owner }}/phosphor" not in workflow
    assert "agent-governor" not in project["dependencies"]
    assert "COPY agent-governor" not in dockerfile
    assert "COPY receipt-kernel" not in dockerfile
    assert "@openai/codex@${CODEX_VERSION}" in dockerfile
    assert "CODEX_BINARY" not in codex_compose
    assert "auth.json:ro" not in codex_compose
    required_scripts = {
        "marginalia-server": "gov_webui.adapter:main",
        "marginalia-generation-executor": "gov_webui.generation_executor_cli:main",
        "marginalia-generation-worker": "gov_webui.generation_worker:main",
        "marginalia-generation-secrets": "gov_webui.generation_secrets:main",
        "marginalia-ag-provider-config": "gov_webui.ag_provider_config:main",
    }
    assert required_scripts.items() <= project["scripts"].items()
    assert "AG_NG_CONTRACT_COMMIT" in dockerfile
    assert "DOCKET_CONTRACT_COMMIT" in dockerfile
    assert "/usr/local/bin/ag-loopctl" in dockerfile
    assert "/usr/local/bin/ag-providerd" in dockerfile
    assert "/usr/local/bin/ag-providerctl" in dockerfile
    assert "/usr/local/bin/docket" in dockerfile
    assert f'MARGINALIA_VERSION="{project["version"]}"' in (REPO_ROOT / "marginalia").read_text()
    assert (
        'DEFAULT_IMAGE="ghcr.io/unpingable/marginalia:${MARGINALIA_VERSION}"'
        in (REPO_ROOT / "marginalia").read_text()
    )
    assert f"marginalia:{project['version']}" in (REPO_ROOT / "docker-compose.yml").read_text()


def test_compose_isolates_ag_ng_credentials_and_uses_one_image() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    build = (REPO_ROOT / "docker-compose.build.yml").read_text()
    web_section, worker_section = compose.split("  marginalia-generation:", 1)
    worker_section, providerd_section = worker_section.split("  marginalia-providerd:", 1)
    providerd_section = providerd_section.split("  marginalia-backup:", 1)[0]

    assert "providerd-secrets" not in web_section
    assert "marginalia-ag-issuer.pk8" not in web_section
    assert "providerd-secrets" not in worker_section
    assert "marginalia-ag-issuer.pk8" in worker_section
    assert "providerctl/rpc.pk8" in worker_section
    assert "providerd-secrets" in providerd_section
    assert "marginalia-evidence-keys.json" not in providerd_section
    assert "generation-worker-entrypoint.sh" in worker_section
    for service in (
        "marginalia:",
        "marginalia-backup:",
        "marginalia-generation:",
        "marginalia-providerd:",
        "marginalia-synthetic:",
    ):
        assert service in build


def test_start_scripts_enable_the_direct_nfs_backup_override() -> None:
    nfs_compose = (REPO_ROOT / "docker-compose.nfs.yml").read_text()
    compose_files = (REPO_ROOT / "compose-files.sh").read_text()
    assert "type: nfs" in nfs_compose
    assert "MARGINALIA_BACKUP_NFS_HOST" in nfs_compose
    assert "MARGINALIA_BACKUP_NFS_EXPORT" in nfs_compose
    assert "marginalia_backups_nfs:/backups" in nfs_compose
    assert 'source "$SCRIPT_DIR/.env"' not in compose_files
    assert "IFS='=' read -r dotenv_key dotenv_value" in compose_files
    assert "MARGINALIA_BACKUP_NFS_HOST:?" in compose_files
    assert "MARGINALIA_BACKUP_NFS_EXPORT:?" in compose_files

    for launcher in ("start.sh", "start-codex.sh"):
        script = (REPO_ROOT / launcher).read_text()
        assert 'source "$SCRIPT_DIR/compose-files.sh"' in script
