#!/usr/bin/env bash

# Docker Compose reads .env for interpolation, but shell conditionals do not.
# Read only the two routing keys needed to select the direct-NFS override; do
# not source or evaluate the dotenv file.
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  while IFS='=' read -r dotenv_key dotenv_value; do
    dotenv_value="${dotenv_value%$'\r'}"
    case "$dotenv_key" in
      MARGINALIA_BACKUP_NFS_HOST)
        if [[ -z "${MARGINALIA_BACKUP_NFS_HOST:-}" ]]; then
          export MARGINALIA_BACKUP_NFS_HOST="$dotenv_value"
        fi
        ;;
      MARGINALIA_BACKUP_NFS_EXPORT)
        if [[ -z "${MARGINALIA_BACKUP_NFS_EXPORT:-}" ]]; then
          export MARGINALIA_BACKUP_NFS_EXPORT="$dotenv_value"
        fi
        ;;
    esac
  done < "$SCRIPT_DIR/.env"
fi

COMPOSE_FILES=(-f docker-compose.yml)
if [[ -n "${MARGINALIA_BACKUP_NFS_HOST:-}${MARGINALIA_BACKUP_NFS_EXPORT:-}" ]]; then
  : "${MARGINALIA_BACKUP_NFS_HOST:?set MARGINALIA_BACKUP_NFS_HOST}"
  : "${MARGINALIA_BACKUP_NFS_EXPORT:?set MARGINALIA_BACKUP_NFS_EXPORT}"
  COMPOSE_FILES+=(-f docker-compose.nfs.yml)
fi
