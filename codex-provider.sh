#!/usr/bin/env bash
# Preserve Agent Governor's qualified command boundary while Marginalia
# dispatches one explicit configured model or the existing native Codex CLI.

set -euo pipefail

exec python3 -m gov_webui.provider_supervisor "$@"
