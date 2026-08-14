# SPDX-License-Identifier: Apache-2.0
"""M1.5a local-appliance distribution regressions."""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from hashlib import sha256
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AG_ROOT = Path(
    os.environ.get("MARGINALIA_AG_SOURCE_DIR", REPO_ROOT.parents[1] / "agent_gov")
).resolve()


def test_sync_stages_the_complete_qualified_ag_distribution(tmp_path: Path) -> None:
    probe = tmp_path / "marginalia-source"
    probe.mkdir()
    shutil.copy2(REPO_ROOT / "sync-deps.sh", probe / "sync-deps.sh")
    shutil.copy2(REPO_ROOT / "AG_CONTRACT_COMMIT", probe / "AG_CONTRACT_COMMIT")

    environment = os.environ.copy()
    environment["MARGINALIA_AG_SOURCE_DIR"] = str(AG_ROOT)
    subprocess.run(
        ["bash", str(probe / "sync-deps.sh")],
        cwd=probe,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    staged = probe / "agent-governor"
    assert (staged / "AG_CONTRACT_COMMIT").read_text().strip() == (
        REPO_ROOT / "AG_CONTRACT_COMMIT"
    ).read_text().strip()
    for package in ("governor", "fiction_governor", "nonfiction_governor", "ops_governor"):
        assert (staged / "src" / package / "__init__.py").is_file()
    assert (staged / "src" / "fiction_governor" / "canon_capture.py").is_file()
    assert (probe / "receipt-kernel" / "src" / "receipt_kernel" / "__init__.py").is_file()
    assert (probe / "receipt-v1" / "src" / "receipt_v1" / "__init__.py").is_file()


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


def test_launcher_starts_one_loopback_bound_codex_appliance(tmp_path: Path) -> None:
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
        check=True,
        capture_output=True,
        text=True,
    )

    calls = log.read_text(encoding="utf-8")
    assert "Marginalia: http://127.0.0.1:8123" in result.stdout
    assert "image inspect marginalia:test" in calls
    assert "--publish 127.0.0.1:8123:8000" in calls
    assert "--env BACKEND_TYPE=codex" in calls
    assert "--env GOVERNOR_CONTEXT_ID=installer-acceptance" in calls
    assert "--volume marginalia-test-data:/data" in calls
    assert "--volume marginalia-test-codex:/root/.codex" in calls
    assert "--label io.marginalia.managed=true" in calls
    assert "/home/" not in calls


def test_launcher_update_can_reuse_a_verified_local_image(tmp_path: Path) -> None:
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

    subprocess.run(
        [str(REPO_ROOT / "marginalia"), "update", "--no-pull", "--no-open"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    calls = log.read_text(encoding="utf-8")
    assert "image inspect marginalia:test" in calls
    assert "pull marginalia:test" not in calls
    assert "stop marginalia-acceptance" in calls
    assert "rm marginalia-acceptance" in calls


def test_installer_can_install_the_release_launcher_without_source_checkout(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "local-bin"
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "MARGINALIA_INSTALL_DIR": str(install_dir),
            "MARGINALIA_INSTALL_SOURCE": str(REPO_ROOT / "marginalia"),
            "MARGINALIA_INSTALL_SHA256": sha256(
                (REPO_ROOT / "marginalia").read_bytes()
            ).hexdigest(),
            "MARGINALIA_INSTALL_ONLY": "1",
        }
    )

    subprocess.run(
        ["sh", str(REPO_ROOT / "install-marginalia.sh")],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    installed = install_dir / "marginalia"
    assert installed.is_file()
    assert installed.stat().st_mode & 0o111
    version = subprocess.run(
        [str(installed), "version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert version.stdout.strip() == "Marginalia 0.1.0"


def test_release_contract_names_and_pins_the_complete_marginalia_appliance() -> None:
    workflow = (REPO_ROOT / ".github/workflows/publish-image.yml").read_text()
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    codex_compose = (REPO_ROOT / "docker-compose.codex.yml").read_text()
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]

    assert "github.repository_owner }}/marginalia" in workflow
    assert "ref: marginalia-chat-contract-m0" in workflow
    assert "linux/amd64,linux/arm64" in workflow
    assert "docker/setup-qemu-action@v3" in workflow
    assert "cp -R _agent_gov/src agent-governor/" in workflow
    assert "_agent_gov/libs/receipt_kernel/src/receipt_kernel" in workflow
    assert "IMAGE_NAME: ${{ github.repository_owner }}/phosphor" not in workflow
    assert "import fiction_governor" in dockerfile
    assert "import fiction_governor, governor, receipt_kernel, receipt_v1" in dockerfile
    assert "@openai/codex@${CODEX_VERSION}" in dockerfile
    assert "CODEX_BINARY" not in codex_compose
    assert "auth.json:ro" not in codex_compose
    assert project["scripts"] == {"marginalia-server": "gov_webui.adapter:main"}
    assert f'MARGINALIA_VERSION="{project["version"]}"' in (
        REPO_ROOT / "marginalia"
    ).read_text()
    assert 'DEFAULT_IMAGE="ghcr.io/unpingable/marginalia:${MARGINALIA_VERSION}"' in (
        REPO_ROOT / "marginalia"
    ).read_text()
    assert f'VERSION="{project["version"]}"' in (
        REPO_ROOT / "install-marginalia.sh"
    ).read_text()
    assert f"marginalia:{project['version']}" in (
        REPO_ROOT / "docker-compose.yml"
    ).read_text()
