#!/usr/bin/env bash
# Exécute Gitleaks via Docker (usage local, sans installation) :
#   GITLEAKS=scripts/gitleaks_docker.sh scripts/secrets_scan.sh source
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash (Windows) : ne pas réécrire les chemins /repo
workdir=$(pwd -W 2>/dev/null || pwd)
exec docker run --rm -v "${workdir}:/repo" -w /repo --entrypoint sh \
  zricethezav/gitleaks:v8.21.2 \
  -c 'git config --global --add safe.directory /repo >/dev/null 2>&1; exec gitleaks "$@"' \
  gitleaks "$@"
