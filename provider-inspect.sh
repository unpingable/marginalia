#!/usr/bin/env bash
# Secret-safe operational container inspection. Never prints Env or mount sources.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 CONTAINER [CONTAINER ...]" >&2
  exit 2
fi

for container in "$@"; do
  if [[ ! "$container" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]]; then
    echo "refusing unsafe container name" >&2
    exit 2
  fi
  docker inspect --format '{{json .Name}} {{json .Config.Image}} {{json .Image}} {{json .State.Status}} {{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}} {{json .RestartCount}} {{json .State.StartedAt}} {{json .HostConfig.Runtime}} {{range .Mounts}}{{json .Destination}}:rw={{json .RW}} {{end}}' "$container"
done
